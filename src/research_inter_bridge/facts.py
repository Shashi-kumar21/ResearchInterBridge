"""Deterministic fact extraction from document text.

No LLM is involved: these are strict patterns with a confidence value, and every
fact lands in ``profile_facts`` as ``pending`` until the student confirms it
(``task.md`` §3.3: never fabricate grades, skills or experience). A missed fact
is a better failure than an invented one, so the patterns are deliberately
narrow.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

from . import profiles

MAX_FACTS = 40


@dataclass(frozen=True, slots=True)
class ExtractedFact:
    """One candidate fact read from a document."""

    field_name: str
    value: str
    confidence: float


_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")

#: International-ish phone numbers: 10-15 digits, optional +, spaces, dashes,
#: dots and parentheses. Rejects long numeric ids through the digit bound.
_PHONE_RE = re.compile(
    r"(?<![\d.])(\+?\d{1,3}[\s.\-]?)?(?:\(\d{1,4}\)[\s.\-]?)?\d{3,5}[\s.\-]?\d{3,5}"
    r"(?:[\s.\-]?\d{1,4})?(?![\d.])"
)

_GPA_RE = re.compile(
    r"(?i)\b(?:cgpa|gpa)\s*[:\-]?\s*(?P<value>\d{1,2}(?:\.\d{1,2})?)\s*(?:/\s*(?P<scale>\d{1,2}(?:\.\d{1,2})?))?"
)

_GRADUATION_RE = re.compile(
    r"(?i)\b(?:expected\s+graduation|graduation|graduating|batch\s+of)\b[^\n]{0,40}?(?P<year>20\d{2})"
)


def _digits(value: str) -> str:
    return re.sub(r"\D", "", value)


def extract_facts(text: str) -> tuple[ExtractedFact, ...]:
    """Return candidate facts, deduplicated and capped at :data:`MAX_FACTS`."""

    if not text:
        return ()

    facts: list[ExtractedFact] = []
    seen: set[tuple[str, str]] = set()

    def add(field_name: str, value: str, confidence: float) -> None:
        key = (field_name, value.casefold())
        if key in seen or len(facts) >= MAX_FACTS:
            return
        seen.add(key)
        facts.append(ExtractedFact(field_name, value, confidence))

    for match in _EMAIL_RE.finditer(text):
        add("contact_email", match.group(0).lower(), 0.9)

    for match in _PHONE_RE.finditer(text):
        raw = match.group(0).strip()
        digits = _digits(raw)
        if 10 <= len(digits) <= 15:
            add("phone", raw, 0.55)

    for match in _GPA_RE.finditer(text):
        value = match.group("value")
        scale = match.group("scale")
        rendered = f"{value}/{scale}" if scale else value
        add("gpa", rendered, 0.8 if scale else 0.6)

    for match in _GRADUATION_RE.finditer(text):
        add("expected_graduation_year", match.group("year"), 0.6)

    return tuple(facts)


def store_facts(
    connection: sqlite3.Connection,
    *,
    user_id: int,
    document_id: int,
    facts: tuple[ExtractedFact, ...],
) -> tuple[int, ...]:
    """Persist facts as ``pending`` rows sourced from one document.

    Idempotent: re-parsing the same document hits the unique
    ``(user_id, field, value, source)`` constraint and returns the existing ids.
    """

    source = f"document:{document_id}"
    ids: list[int] = []
    for fact in facts:
        ids.append(
            profiles.add_profile_fact(
                connection,
                user_id=user_id,
                field_name=fact.field_name,
                value=fact.value,
                source=source,
                status="pending",
                confidence=fact.confidence,
            )
        )
    return tuple(ids)
