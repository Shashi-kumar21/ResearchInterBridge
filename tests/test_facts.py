"""Deterministic fact extraction behaviour (no LLM, no guessing)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from research_inter_bridge import db, facts
from research_inter_bridge.facts import ExtractedFact, extract_facts, store_facts
from research_inter_bridge.profiles import create_user, list_profile_facts


@pytest.fixture
def connection(tmp_path: Path):
    with db.session(tmp_path / "app.db") as conn:
        db.apply_migrations(conn)
        yield conn


def _values(field_name: str, text: str) -> list[str]:
    return [fact.value for fact in extract_facts(text) if fact.field_name == field_name]


def test_email_is_extracted_and_lowercased() -> None:
    assert _values("contact_email", "Contact: Asha.Verma@Example.EDU") == ["asha.verma@example.edu"]


def test_gpa_with_scale_keeps_the_scale() -> None:
    assert _values("gpa", "CGPA: 8.9/10") == ["8.9/10"]
    assert _values("gpa", "GPA - 3.72 / 4.00") == ["3.72/4.00"]


def test_gpa_without_scale_is_kept_but_less_confident() -> None:
    (fact,) = [f for f in extract_facts("GPA: 9.1") if f.field_name == "gpa"]
    assert fact.value == "9.1"
    assert fact.confidence < 0.8


def test_phone_numbers_are_extracted_with_country_code() -> None:
    values = _values("phone", "Phone: +91 98765 43210")
    assert values == ["+91 98765 43210"]


def test_roll_numbers_and_years_are_not_phones() -> None:
    assert _values("phone", "Roll No: 2021CS1234\nBatch 2027") == []


def test_long_digit_runs_are_not_phones() -> None:
    assert _values("phone", "Application id 1234567890123456") == []


def test_graduation_year_is_extracted() -> None:
    assert _values("expected_graduation_year", "Expected graduation: May 2027") == ["2027"]
    assert _values("expected_graduation_year", "Batch of 2028") == ["2028"]


def test_duplicate_mentions_are_collapsed() -> None:
    text = "asha@example.edu\nASHA@example.edu\nasha@example.edu"
    assert _values("contact_email", text) == ["asha@example.edu"]


def test_empty_text_yields_no_facts() -> None:
    assert extract_facts("") == ()
    assert extract_facts("   \n\t ") == ()


def test_fact_count_is_capped() -> None:
    text = "\n".join(f"user{i}@example.edu" for i in range(100))
    assert len(extract_facts(text)) == facts.MAX_FACTS


def test_store_facts_is_idempotent_and_pending(connection: sqlite3.Connection) -> None:
    user_id = create_user(connection)
    extracted = (
        ExtractedFact("gpa", "8.9/10", 0.8),
        ExtractedFact("phone", "+91 98765 43210", 0.55),
    )

    first = store_facts(connection, user_id=user_id, document_id=7, facts=extracted)
    second = store_facts(connection, user_id=user_id, document_id=7, facts=extracted)

    assert first == second
    stored = list_profile_facts(connection, user_id)
    assert {fact["status"] for fact in stored} == {"pending"}
    assert {fact["source"] for fact in stored} == {"document:7"}


def test_facts_from_two_documents_stay_separate(connection: sqlite3.Connection) -> None:
    user_id = create_user(connection)
    store_facts(
        connection,
        user_id=user_id,
        document_id=1,
        facts=(ExtractedFact("gpa", "8.9/10", 0.8),),
    )
    store_facts(
        connection,
        user_id=user_id,
        document_id=2,
        facts=(ExtractedFact("gpa", "8.4/10", 0.8),),
    )

    sources = {fact["source"] for fact in list_profile_facts(connection, user_id)}
    assert sources == {"document:1", "document:2"}
