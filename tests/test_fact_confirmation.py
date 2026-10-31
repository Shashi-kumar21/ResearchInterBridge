"""Confirmation workflow: conflicts, corrections, rejection and apply-to-profile."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from research_inter_bridge import db, facts
from research_inter_bridge.errors import ProfileError, ProfileNotFoundError
from research_inter_bridge.facts import (
    ExtractedFact,
    FactCandidate,
    apply_confirmed_facts_to_profile,
    confirm_fact,
    fact_groups,
    reject_fact,
    store_facts,
)
from research_inter_bridge.profiles import (
    StudentProfile,
    confirmed_facts,
    create_user,
    list_profile_facts,
    load_profile,
    save_profile,
)


@pytest.fixture
def connection(tmp_path: Path):
    with db.session(tmp_path / "app.db") as conn:
        db.apply_migrations(conn)
        yield conn


@pytest.fixture
def user_id(connection: sqlite3.Connection) -> int:
    return create_user(connection)


def _profile(user_id: int, **overrides) -> StudentProfile:
    values = {"user_id": user_id, "full_name": "Asha Verma", "contact_email": "asha@example.edu"}
    values.update(overrides)
    return StudentProfile(**values)


def _add(connection: sqlite3.Connection, user_id: int, field: str, value: str, source: str) -> int:
    return store_facts(
        connection,
        user_id=user_id,
        document_id=int(source.split(":")[1]),
        facts=(ExtractedFact(field, value, 0.8),),
    )[0]


def _candidate_ids(group: facts.FactGroup, status: str = "pending") -> list[int]:
    candidates: tuple[FactCandidate, ...] = getattr(group, status)
    return [candidate.id for candidate in candidates]


def _group(connection: sqlite3.Connection, user_id: int, field: str) -> facts.FactGroup:
    (group,) = [g for g in fact_groups(connection, user_id) if g.field_name == field]
    return group


# ------------------------------------------------------------------ conflicts


def test_two_documents_disagreeing_is_flagged_as_a_conflict(
    connection: sqlite3.Connection, user_id: int
) -> None:
    _add(connection, user_id, "gpa", "8.9/10", "document:1")
    _add(connection, user_id, "gpa", "8.4/10", "document:2")

    group = _group(connection, user_id, "gpa")

    assert group.has_conflict is True
    assert {candidate.value for candidate in group.pending} == {"8.9/10", "8.4/10"}
    assert group.confirmed == ()


def test_same_value_from_two_documents_is_not_a_conflict(
    connection: sqlite3.Connection, user_id: int
) -> None:
    _add(connection, user_id, "gpa", "8.9/10", "document:1")
    _add(connection, user_id, "gpa", "8.9/10", "document:2")

    assert _group(connection, user_id, "gpa").has_conflict is False


# ----------------------------------------------------------------- confirmation


def test_confirming_one_candidate_rejects_the_competitors(
    connection: sqlite3.Connection, user_id: int
) -> None:
    chosen = _add(connection, user_id, "gpa", "8.9/10", "document:1")
    other = _add(connection, user_id, "gpa", "8.4/10", "document:2")

    assert confirm_fact(connection, user_id=user_id, fact_id=chosen, actor="student:1") is True

    group = _group(connection, user_id, "gpa")
    assert group.has_conflict is False
    assert _candidate_ids(group, "confirmed") == [chosen]
    assert group.pending == ()
    statuses = {fact["id"]: fact["status"] for fact in list_profile_facts(connection, user_id)}
    assert statuses[other] == "rejected"


def test_confirming_twice_is_idempotent(connection: sqlite3.Connection, user_id: int) -> None:
    fact_id = _add(connection, user_id, "contact_email", "a@example.edu", "document:1")

    assert confirm_fact(connection, user_id=user_id, fact_id=fact_id, actor="student:1")
    assert confirm_fact(connection, user_id=user_id, fact_id=fact_id, actor="student:1")

    actions = [event.action for event in db.list_audit_events(connection)]
    assert actions.count("profile.fact_confirmed") == 1


def test_correction_replaces_the_extracted_value(
    connection: sqlite3.Connection, user_id: int
) -> None:
    fact_id = _add(connection, user_id, "gpa", "8.9/10", "document:1")

    confirm_fact(
        connection, user_id=user_id, fact_id=fact_id, actor="student:1", corrected_value="9.1/10"
    )

    (fact,) = confirmed_facts(connection, user_id)
    assert fact["value"] == "9.1/10"
    event = [e for e in db.list_audit_events(connection) if e.action == "profile.fact_confirmed"][0]
    assert event.metadata["corrected"] is True
    assert "9.1" not in repr(event.metadata)


@pytest.mark.parametrize("bad", ["", "   ", "x" * 200])
def test_correction_must_be_a_reasonable_value(
    connection: sqlite3.Connection, user_id: int, bad: str
) -> None:
    fact_id = _add(connection, user_id, "gpa", "8.9/10", "document:1")

    with pytest.raises(ProfileError, match="corrected fact value"):
        confirm_fact(
            connection, user_id=user_id, fact_id=fact_id, actor="student:1", corrected_value=bad
        )


def test_confirming_another_users_fact_is_impossible(
    connection: sqlite3.Connection, user_id: int
) -> None:
    stranger = create_user(connection)
    fact_id = _add(connection, user_id, "gpa", "8.9/10", "document:1")

    assert confirm_fact(connection, user_id=stranger, fact_id=fact_id, actor="student:2") is False
    assert {f["status"] for f in list_profile_facts(connection, user_id)} == {"pending"}


def test_confirming_an_unknown_fact_returns_false(
    connection: sqlite3.Connection, user_id: int
) -> None:
    assert confirm_fact(connection, user_id=user_id, fact_id=999, actor="student:1") is False


# ---------------------------------------------------------------------- reject


def test_rejected_fact_is_hidden_from_groups(connection: sqlite3.Connection, user_id: int) -> None:
    fact_id = _add(connection, user_id, "phone", "+91 98765 43210", "document:1")

    assert reject_fact(connection, user_id=user_id, fact_id=fact_id, actor="student:1") is True

    assert fact_groups(connection, user_id) == ()
    actions = [event.action for event in db.list_audit_events(connection)]
    assert "profile.fact_rejected" in actions


def test_confirmed_fact_cannot_be_rejected(connection: sqlite3.Connection, user_id: int) -> None:
    fact_id = _add(connection, user_id, "gpa", "8.9/10", "document:1")
    confirm_fact(connection, user_id=user_id, fact_id=fact_id, actor="student:1")

    with pytest.raises(ProfileError, match="confirmed fact cannot be rejected"):
        reject_fact(connection, user_id=user_id, fact_id=fact_id, actor="student:1")


def test_rejecting_another_users_fact_returns_false(
    connection: sqlite3.Connection, user_id: int
) -> None:
    stranger = create_user(connection)
    fact_id = _add(connection, user_id, "gpa", "8.9/10", "document:1")

    assert reject_fact(connection, user_id=stranger, fact_id=fact_id, actor="student:2") is False


# ----------------------------------------------------------------------- apply


def test_apply_fills_blank_profile_fields(connection: sqlite3.Connection, user_id: int) -> None:
    save_profile(connection, _profile(user_id), actor="student:1")
    for field, value in (("phone", "+91 98765 43210"), ("gpa", "8.9/10")):
        fact_id = _add(connection, user_id, field, value, "document:1")
        confirm_fact(connection, user_id=user_id, fact_id=fact_id, actor="student:1")

    applied = apply_confirmed_facts_to_profile(connection, user_id=user_id, actor="student:1")

    stored = load_profile(connection, user_id)
    assert applied == ("gpa", "gpa_scale", "phone")
    assert stored.phone == "+91 98765 43210"
    assert stored.gpa == 8.9
    assert stored.gpa_scale == 10.0


def test_apply_leaves_the_required_email_alone_unless_overwriting(
    connection: sqlite3.Connection, user_id: int
) -> None:
    save_profile(connection, _profile(user_id), actor="student:1")
    fact_id = _add(connection, user_id, "contact_email", "asha.verma@example.edu", "document:1")
    confirm_fact(connection, user_id=user_id, fact_id=fact_id, actor="student:1")

    assert apply_confirmed_facts_to_profile(connection, user_id=user_id, actor="student:1") == ()
    assert load_profile(connection, user_id).contact_email == "asha@example.edu"

    applied = apply_confirmed_facts_to_profile(
        connection, user_id=user_id, actor="student:1", overwrite=True
    )
    assert applied == ("contact_email",)
    assert load_profile(connection, user_id).contact_email == "asha.verma@example.edu"


def test_apply_does_not_overwrite_a_typed_value_by_default(
    connection: sqlite3.Connection, user_id: int
) -> None:
    save_profile(
        connection,
        _profile(user_id, phone="+91 90000 00000"),
        actor="student:1",
    )
    fact_id = _add(connection, user_id, "phone", "+91 98765 43210", "document:1")
    confirm_fact(connection, user_id=user_id, fact_id=fact_id, actor="student:1")

    applied = apply_confirmed_facts_to_profile(connection, user_id=user_id, actor="student:1")

    assert applied == ()
    assert load_profile(connection, user_id).phone == "+91 90000 00000"


def test_apply_can_overwrite_when_explicitly_requested(
    connection: sqlite3.Connection, user_id: int
) -> None:
    save_profile(connection, _profile(user_id, phone="+91 90000 00000"), actor="student:1")
    fact_id = _add(connection, user_id, "phone", "+91 98765 43210", "document:1")
    confirm_fact(connection, user_id=user_id, fact_id=fact_id, actor="student:1")

    applied = apply_confirmed_facts_to_profile(
        connection, user_id=user_id, actor="student:1", overwrite=True
    )

    assert applied == ("phone",)
    assert load_profile(connection, user_id).phone == "+91 98765 43210"


def test_graduation_year_is_not_applied_to_a_date_field(
    connection: sqlite3.Connection, user_id: int
) -> None:
    save_profile(connection, _profile(user_id), actor="student:1")
    fact_id = _add(connection, user_id, "expected_graduation_year", "2027", "document:1")
    confirm_fact(connection, user_id=user_id, fact_id=fact_id, actor="student:1")

    applied = apply_confirmed_facts_to_profile(connection, user_id=user_id, actor="student:1")

    assert applied == ()
    assert load_profile(connection, user_id).expected_graduation is None


def test_apply_requires_a_profile(connection: sqlite3.Connection, user_id: int) -> None:
    fact_id = _add(connection, user_id, "contact_email", "a@example.edu", "document:1")
    confirm_fact(connection, user_id=user_id, fact_id=fact_id, actor="student:1")

    with pytest.raises(ProfileNotFoundError):
        apply_confirmed_facts_to_profile(connection, user_id=user_id, actor="student:1")


def test_apply_without_confirmed_facts_does_nothing(
    connection: sqlite3.Connection, user_id: int
) -> None:
    save_profile(connection, _profile(user_id), actor="student:1")
    _add(connection, user_id, "gpa", "8.9/10", "document:1")

    assert apply_confirmed_facts_to_profile(connection, user_id=user_id, actor="student:1") == ()


def test_apply_keeps_the_profile_scale_when_the_fact_has_none(
    connection: sqlite3.Connection, user_id: int
) -> None:
    save_profile(connection, _profile(user_id, gpa_scale=10.0), actor="student:1")
    fact_id = _add(connection, user_id, "gpa", "9.1", "document:1")
    confirm_fact(connection, user_id=user_id, fact_id=fact_id, actor="student:1")

    apply_confirmed_facts_to_profile(connection, user_id=user_id, actor="student:1")

    stored = load_profile(connection, user_id)
    assert stored.gpa == 9.1
    assert stored.gpa_scale == 10.0
