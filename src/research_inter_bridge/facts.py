"""Deterministic fact extraction and the student confirmation workflow.

No LLM is involved: these are strict patterns with a confidence value, and every
fact lands in ``profile_facts`` as ``pending`` until the student confirms it
(``task.md`` §3.3: never fabricate grades, skills or experience). A missed fact
is a better failure than an invented one, so the patterns are deliberately
narrow.

The second half of this module is the confirmation workflow:

* :func:`fact_groups` groups candidates per field and flags conflicts, so two
  documents that disagree are shown side by side instead of one silently winning.
* :func:`confirm_fact` records the student's choice, optionally with a correction,
  and rejects the competing candidates for that field.
* :func:`apply_confirmed_facts_to_profile` copies confirmed facts into the profile
  only when the student asks for it and never overwrites a value they typed.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from typing import Any

from . import db, profiles
from .errors import ProfileError

MAX_FACTS = 40
MAX_FACT_VALUE_LENGTH = 120


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


# --------------------------------------------------------------- confirmation


_GPA_VALUE_RE = re.compile(
    r"^(?P<value>\d{1,2}(?:\.\d{1,2})?)(?:\s*/\s*(?P<scale>\d{1,2}(?:\.\d{1,2})?))?$"
)

#: Fact fields that :func:`apply_confirmed_facts_to_profile` can copy into the
#: profile. Graduation year is intentionally absent: the profile stores a date and
#: a year alone cannot be turned into one without inventing day/month.
APPLICABLE_FIELDS: frozenset[str] = frozenset({"contact_email", "phone", "gpa"})


@dataclass(frozen=True, slots=True)
class FactCandidate:
    """One stored fact row, as shown to the student for confirmation."""

    id: int
    field_name: str
    value: str
    source: str
    status: str
    confidence: float | None


@dataclass(frozen=True, slots=True)
class FactGroup:
    """All non-rejected candidates for one field."""

    field_name: str
    pending: tuple[FactCandidate, ...]
    confirmed: tuple[FactCandidate, ...]

    @property
    def has_conflict(self) -> bool:
        """True when two documents propose different values and none is confirmed."""

        if self.confirmed:
            return False
        return len({candidate.value.casefold() for candidate in self.pending}) > 1


def _row_to_candidate(row: sqlite3.Row) -> FactCandidate:
    return FactCandidate(
        id=int(row["id"]),
        field_name=row["field"],
        value=row["value"],
        source=row["source"],
        status=row["status"],
        confidence=row["confidence"],
    )


_FACT_COLUMNS = "id, field, value, source, status, confidence"


def fact_groups(connection: sqlite3.Connection, user_id: int) -> tuple[FactGroup, ...]:
    """Return confirmable facts grouped by field, conflicts included.

    Rejected facts are excluded: they are history, not candidates.
    """

    rows = connection.execute(
        f"SELECT {_FACT_COLUMNS} FROM profile_facts "  # noqa: S608
        "WHERE user_id = ? AND status != 'rejected' ORDER BY field, id",
        (user_id,),
    ).fetchall()

    buckets: dict[str, list[FactCandidate]] = {}
    for row in rows:
        buckets.setdefault(row["field"], []).append(_row_to_candidate(row))

    groups: list[FactGroup] = []
    for field_name in sorted(buckets):
        candidates = buckets[field_name]
        groups.append(
            FactGroup(
                field_name=field_name,
                pending=tuple(c for c in candidates if c.status == "pending"),
                confirmed=tuple(c for c in candidates if c.status == "confirmed"),
            )
        )
    return tuple(groups)


def _clean_fact_value(value: str) -> str:
    cleaned = " ".join(str(value).split())
    if not cleaned:
        raise ProfileError("a corrected fact value cannot be blank.")
    if len(cleaned) > MAX_FACT_VALUE_LENGTH:
        raise ProfileError(
            f"a corrected fact value must be at most {MAX_FACT_VALUE_LENGTH} characters."
        )
    return cleaned


def confirm_fact(
    connection: sqlite3.Connection,
    *,
    user_id: int,
    fact_id: int,
    actor: str,
    corrected_value: str | None = None,
) -> bool:
    """Confirm one fact for its owner, optionally replacing the extracted value.

    Confirming a value rejects the competing *pending* candidates for the same
    field, so a field cannot end up with two confirmed truths. The audit event
    records the field name and whether a correction was supplied - never the
    value itself, which may be personal data.
    """

    row = connection.execute(
        f"SELECT {_FACT_COLUMNS} FROM profile_facts WHERE id = ? AND user_id = ?",  # noqa: S608
        (fact_id, user_id),
    ).fetchone()
    if row is None:
        return False
    if row["status"] == "confirmed":
        return True

    value = row["value"] if corrected_value is None else _clean_fact_value(corrected_value)
    superseded = 0
    try:
        with db.transaction(connection):
            connection.execute(
                "UPDATE profile_facts SET status = 'confirmed', value = ? "
                "WHERE id = ? AND user_id = ?",
                (value, fact_id, user_id),
            )
            cursor = connection.execute(
                "UPDATE profile_facts SET status = 'rejected' "
                "WHERE user_id = ? AND field = ? AND id != ? AND status = 'pending'",
                (user_id, row["field"], fact_id),
            )
            superseded = cursor.rowcount
            db.record_audit_event(
                connection,
                actor=actor,
                action="profile.fact_confirmed",
                outcome="success",
                target_type="profile_fact",
                target_id=str(fact_id),
                metadata={
                    "field": row["field"],
                    "corrected": corrected_value is not None,
                    "superseded": superseded,
                },
            )
    except sqlite3.IntegrityError as exc:
        raise ProfileError(
            "the corrected value already exists for this fact's source; "
            "reject the duplicate candidate instead of editing this one."
        ) from exc
    return True


def reject_fact(connection: sqlite3.Connection, *, user_id: int, fact_id: int, actor: str) -> bool:
    """Reject one pending fact for its owner. Confirmed facts cannot be rejected."""

    row = connection.execute(
        "SELECT status FROM profile_facts WHERE id = ? AND user_id = ?", (fact_id, user_id)
    ).fetchone()
    if row is None:
        return False
    if row["status"] == "rejected":
        return True
    if row["status"] == "confirmed":
        raise ProfileError(
            "a confirmed fact cannot be rejected; confirm a different candidate instead."
        )

    with db.transaction(connection):
        connection.execute(
            "UPDATE profile_facts SET status = 'rejected' WHERE id = ? AND user_id = ?",
            (fact_id, user_id),
        )
        db.record_audit_event(
            connection,
            actor=actor,
            action="profile.fact_rejected",
            outcome="success",
            target_type="profile_fact",
            target_id=str(fact_id),
        )
    return True


def _gpa_updates(value: str, current_scale: float | None) -> dict[str, Any]:
    match = _GPA_VALUE_RE.match(value.strip())
    if match is None:
        raise ProfileError(
            f"GPA fact {value!r} is not a number like 8.9 or 8.9/10; "
            "correct the value before applying it."
        )
    gpa = float(match.group("value"))
    scale = match.group("scale")
    updates: dict[str, Any] = {"gpa": gpa}
    if scale is not None:
        updates["gpa_scale"] = float(scale)
    elif current_scale is None:
        # Without a scale the number cannot be validated against 4.0 or 10.0.
        # Keep the profile's scale empty rather than guessing one.
        updates["gpa_scale"] = None
    return updates


def apply_confirmed_facts_to_profile(
    connection: sqlite3.Connection,
    *,
    user_id: int,
    actor: str,
    fields: frozenset[str] | None = None,
    overwrite: bool = False,
) -> tuple[str, ...]:
    """Copy confirmed facts into the profile and return the applied field names.

    Defaults are conservative: blank profile fields are filled, values the student
    already typed are left alone unless ``overwrite=True`` is passed explicitly.
    """

    profile = profiles.load_profile(connection, user_id)
    wanted = APPLICABLE_FIELDS if fields is None else (APPLICABLE_FIELDS & fields)
    updates: dict[str, Any] = {}

    for fact in profiles.confirmed_facts(connection, user_id):
        field_name = str(fact["field"])
        if field_name not in wanted or field_name in updates:
            continue
        if not overwrite and getattr(profile, field_name, None) not in (None, ""):
            continue
        value = str(fact["value"])
        if field_name == "gpa":
            updates.update(_gpa_updates(value, profile.gpa_scale))
            continue
        updates[field_name] = value

    if not updates:
        return ()

    stored = profile.with_changes(**updates)
    profiles.save_profile(connection, stored, actor=actor)
    return tuple(sorted(updates))
