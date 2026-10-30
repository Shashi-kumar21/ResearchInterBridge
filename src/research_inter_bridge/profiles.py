"""Student profile model, validation and persistence.

Phase 2 of ``task.md`` (§2 step 2, §7 phase 2). Rules encoded here:

* A profile is per-user data: every read is scoped to a user id, and a missing
  profile raises instead of returning another student's row.
* Validation happens in the domain object, so invalid values fail before SQL.
* Confirmed facts (``profile_facts``) are stored separately from profile fields:
  only confirmed facts may be used in outbound email, and the confirmation
  workflow itself arrives with document ingestion (Phase 2b).
* Profile writes and their audit events share one transaction, so a failure
  cannot leave a profile change without a trail (or the reverse).
"""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field, replace
from datetime import date
from enum import StrEnum
from typing import Any

from . import db
from .errors import ProfileError, ProfileNotFoundError
from .logging import get_logger

logger = get_logger(__name__)

MAX_NAME_LENGTH = 200
MAX_LIST_ITEMS = 50
MAX_ITEM_LENGTH = 120

_EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[A-Za-z]{2,}$")
_YEAR_RANGE = (1, 8)


class DegreeType(StrEnum):
    """Degree types accepted by the product (``task.md`` §2 step 2)."""

    BTECH = "btech"
    BE = "be"
    BS = "bs"
    MSC = "msc"
    MTECH = "mtech"
    DUAL = "dual_degree"
    PHD = "phd"
    OTHER = "other"


def _parse_degree_type(value: DegreeType | str | None) -> DegreeType | None:
    if value is None:
        return None
    if isinstance(value, DegreeType):
        return value
    try:
        return DegreeType(value.strip().lower())
    except ValueError as exc:
        allowed = ", ".join(member.value for member in DegreeType)
        raise ProfileError(f"degree_type must be one of: {allowed}; got {value!r}.") from exc


def _clean_text(
    value: str | None, field_name: str, *, max_length: int = MAX_NAME_LENGTH
) -> str | None:
    if value is None:
        return None
    cleaned = " ".join(value.split())
    if not cleaned:
        return None
    if len(cleaned) > max_length:
        raise ProfileError(f"{field_name} must be at most {max_length} characters.")
    return cleaned


def _clean_required_text(value: str, field_name: str) -> str:
    cleaned = _clean_text(value, field_name)
    if cleaned is None:
        raise ProfileError(f"{field_name} is required and cannot be blank.")
    return cleaned


def _clean_email(value: str) -> str:
    cleaned = _clean_required_text(value, "contact_email").lower()
    if not _EMAIL_RE.match(cleaned):
        raise ProfileError(f"contact_email is not a valid email address: {value!r}.")
    return cleaned


def _clean_list(values: Iterable[str], field_name: str) -> tuple[str, ...]:
    cleaned: list[str] = []
    seen: set[str] = set()
    for raw in values:
        if not isinstance(raw, str):
            raise ProfileError(f"{field_name} entries must be strings; got {type(raw).__name__}.")
        item = " ".join(raw.split())
        if not item:
            continue
        if len(item) > MAX_ITEM_LENGTH:
            preview = item[:40]
            raise ProfileError(
                f"{field_name} entries must be at most {MAX_ITEM_LENGTH} characters: {preview!r}..."
            )
        key = item.casefold()
        if key in seen:
            continue
        seen.add(key)
        cleaned.append(item)
    if len(cleaned) > MAX_LIST_ITEMS:
        raise ProfileError(f"{field_name} may contain at most {MAX_LIST_ITEMS} entries.")
    return tuple(cleaned)


def _parse_date(value: date | str | None, field_name: str) -> date | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value
    try:
        return date.fromisoformat(value.strip())
    except ValueError as exc:
        raise ProfileError(
            f"{field_name} must be an ISO date (YYYY-MM-DD); got {value!r}."
        ) from exc


@dataclass(frozen=True, slots=True)
class StudentProfile:
    """Validated, normalised student profile.

    Fields are optional during onboarding except identity and degree context;
    :func:`completeness` reports what is still missing rather than blocking a
    partially filled profile.
    """

    user_id: int
    full_name: str
    contact_email: str
    preferred_name: str | None = None
    phone: str | None = None
    institution: str | None = None
    department: str | None = None
    country: str | None = None
    degree_type: DegreeType | None = None
    current_year: int | None = None
    expected_graduation: date | None = None
    gpa: float | None = None
    gpa_scale: float | None = None
    coursework: tuple[str, ...] = ()
    skills: tuple[str, ...] = ()
    research_keywords: tuple[str, ...] = ()
    target_countries: tuple[str, ...] = ()
    internship_start: date | None = None
    internship_end: date | None = None
    funding_required: bool | None = None
    consent_given_at: str | None = None
    created_at: str | None = field(default=None, compare=False)
    updated_at: str | None = field(default=None, compare=False)

    def __post_init__(self) -> None:
        if not isinstance(self.user_id, int) or self.user_id <= 0:
            raise ProfileError("user_id must be a positive integer.")
        object.__setattr__(self, "full_name", _clean_required_text(self.full_name, "full_name"))
        object.__setattr__(self, "contact_email", _clean_email(self.contact_email))
        object.__setattr__(
            self, "preferred_name", _clean_text(self.preferred_name, "preferred_name")
        )
        object.__setattr__(self, "phone", _clean_text(self.phone, "phone", max_length=40))
        for name in ("institution", "department", "country"):
            object.__setattr__(self, name, _clean_text(getattr(self, name), name))
        object.__setattr__(self, "degree_type", _parse_degree_type(self.degree_type))

        if self.current_year is not None:
            low, high = _YEAR_RANGE
            if not isinstance(self.current_year, int) or not low <= self.current_year <= high:
                raise ProfileError(
                    f"current_year must be an integer between {low} and {high}; "
                    f"got {self.current_year!r}."
                )

        object.__setattr__(
            self,
            "expected_graduation",
            _parse_date(self.expected_graduation, "expected_graduation"),
        )
        object.__setattr__(
            self, "internship_start", _parse_date(self.internship_start, "internship_start")
        )
        object.__setattr__(
            self, "internship_end", _parse_date(self.internship_end, "internship_end")
        )
        if (
            self.internship_start
            and self.internship_end
            and self.internship_start > self.internship_end
        ):
            raise ProfileError("internship_start must be on or before internship_end.")

        if self.gpa_scale is not None and self.gpa_scale <= 0:
            raise ProfileError(f"gpa_scale must be greater than 0; got {self.gpa_scale!r}.")
        if self.gpa is not None:
            if self.gpa < 0:
                raise ProfileError(f"gpa cannot be negative; got {self.gpa!r}.")
            if self.gpa_scale is not None and self.gpa > self.gpa_scale:
                raise ProfileError(f"gpa ({self.gpa}) cannot exceed gpa_scale ({self.gpa_scale}).")

        for name in ("coursework", "skills", "research_keywords", "target_countries"):
            object.__setattr__(self, name, _clean_list(getattr(self, name), name))

    @property
    def display_name(self) -> str:
        return self.preferred_name or self.full_name

    def with_changes(self, **changes: Any) -> StudentProfile:
        """Return a validated copy with the given fields replaced."""

        return replace(self, **changes)


#: Field -> (human label, weight) used by :func:`completeness`.
COMPLETENESS_FIELDS: dict[str, tuple[str, int]] = {
    "full_name": ("full name", 3),
    "contact_email": ("contact email", 3),
    "institution": ("current institution", 2),
    "department": ("department", 1),
    "country": ("country", 1),
    "degree_type": ("degree type", 2),
    "current_year": ("current year", 1),
    "expected_graduation": ("expected graduation", 1),
    "gpa": ("CGPA/GPA", 1),
    "gpa_scale": ("grading scale", 1),
    "coursework": ("relevant coursework", 1),
    "skills": ("technical skills", 2),
    "research_keywords": ("research interests", 3),
    "target_countries": ("target countries", 1),
    "internship_start": ("internship start date", 2),
    "internship_end": ("internship end date", 1),
    "consent_given_at": ("document-processing consent", 2),
}


@dataclass(frozen=True, slots=True)
class Completeness:
    """Profile completeness used by the dashboard (``task.md`` §14 step 14)."""

    percent: int
    missing: tuple[str, ...]

    @property
    def is_complete(self) -> bool:
        return not self.missing


def completeness(profile: StudentProfile) -> Completeness:
    """Return weighted completeness and the human labels still missing."""

    earned = 0
    total = 0
    missing: list[str] = []
    for name, (label, weight) in COMPLETENESS_FIELDS.items():
        total += weight
        value = getattr(profile, name)
        if value is None or value == () or value == "":
            missing.append(label)
        else:
            earned += weight
    percent = round(100 * earned / total) if total else 100
    return Completeness(percent=percent, missing=tuple(missing))


def create_user(connection: sqlite3.Connection) -> int:
    """Create an empty local user and return its id."""

    cursor = connection.execute("INSERT INTO users (created_at) VALUES (?)", (db.utc_now(),))
    return int(cursor.lastrowid or 0)


def _row_to_profile(row: sqlite3.Row) -> StudentProfile:
    return StudentProfile(
        user_id=int(row["user_id"]),
        full_name=row["full_name"],
        contact_email=row["contact_email"],
        preferred_name=row["preferred_name"],
        phone=row["phone"],
        institution=row["institution"],
        department=row["department"],
        country=row["country"],
        degree_type=row["degree_type"],
        current_year=row["current_year"],
        expected_graduation=row["expected_graduation"],
        gpa=row["gpa"],
        gpa_scale=row["gpa_scale"],
        coursework=tuple(json.loads(row["coursework_json"])),
        skills=tuple(json.loads(row["skills_json"])),
        research_keywords=tuple(json.loads(row["research_keywords_json"])),
        target_countries=tuple(json.loads(row["target_countries_json"])),
        internship_start=row["internship_start"],
        internship_end=row["internship_end"],
        funding_required=None if row["funding_required"] is None else bool(row["funding_required"]),
        consent_given_at=row["consent_given_at"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


_PROFILE_COLUMNS = (
    "user_id",
    "full_name",
    "preferred_name",
    "contact_email",
    "phone",
    "institution",
    "department",
    "country",
    "degree_type",
    "current_year",
    "expected_graduation",
    "gpa",
    "gpa_scale",
    "coursework_json",
    "skills_json",
    "research_keywords_json",
    "target_countries_json",
    "internship_start",
    "internship_end",
    "funding_required",
    "consent_given_at",
    "created_at",
    "updated_at",
)


def _profile_values(
    profile: StudentProfile, *, created_at: str, updated_at: str
) -> tuple[Any, ...]:
    return (
        profile.user_id,
        profile.full_name,
        profile.preferred_name,
        profile.contact_email,
        profile.phone,
        profile.institution,
        profile.department,
        profile.country,
        None if profile.degree_type is None else profile.degree_type.value,
        profile.current_year,
        None if profile.expected_graduation is None else profile.expected_graduation.isoformat(),
        profile.gpa,
        profile.gpa_scale,
        json.dumps(list(profile.coursework)),
        json.dumps(list(profile.skills)),
        json.dumps(list(profile.research_keywords)),
        json.dumps(list(profile.target_countries)),
        None if profile.internship_start is None else profile.internship_start.isoformat(),
        None if profile.internship_end is None else profile.internship_end.isoformat(),
        None if profile.funding_required is None else int(profile.funding_required),
        profile.consent_given_at,
        created_at,
        updated_at,
    )


def save_profile(
    connection: sqlite3.Connection,
    profile: StudentProfile,
    *,
    actor: str,
) -> StudentProfile:
    """Insert or update a profile and record an audit event atomically.

    Returns the stored profile with ``created_at``/``updated_at`` filled in.
    The audit metadata lists field names only - never values, which may contain
    personal data.
    """

    if not connection.execute("SELECT 1 FROM users WHERE id = ?", (profile.user_id,)).fetchone():
        raise ProfileError(f"user {profile.user_id} does not exist; create the user first.")

    now = db.utc_now()
    existing = connection.execute(
        "SELECT created_at FROM student_profiles WHERE user_id = ?", (profile.user_id,)
    ).fetchone()
    created_at = existing["created_at"] if existing else now

    columns = ", ".join(_PROFILE_COLUMNS)
    placeholders = ", ".join("?" for _ in _PROFILE_COLUMNS)
    updates = ", ".join(f"{column} = excluded.{column}" for column in _PROFILE_COLUMNS[1:])
    insert_sql = (
        f"INSERT INTO student_profiles ({columns}) VALUES ({placeholders}) "  # noqa: S608
        f"ON CONFLICT (user_id) DO UPDATE SET {updates}"
    )

    with db.transaction(connection):
        connection.execute(
            insert_sql,
            _profile_values(profile, created_at=created_at, updated_at=now),
        )
        db.record_audit_event(
            connection,
            actor=actor,
            action="profile.updated" if existing else "profile.created",
            outcome="success",
            target_type="student_profile",
            target_id=str(profile.user_id),
            metadata={"fields": sorted(_PROFILE_COLUMNS[1:-2])},
        )

    stored = profile.with_changes(created_at=created_at, updated_at=now)
    logger.info(
        "profile saved user_id=%s action=%s", profile.user_id, "updated" if existing else "created"
    )
    return stored


def load_profile(connection: sqlite3.Connection, user_id: int) -> StudentProfile:
    """Load one user's profile; raises :class:`ProfileNotFoundError` otherwise."""

    select_sql = f"SELECT {', '.join(_PROFILE_COLUMNS)} FROM student_profiles WHERE user_id = ?"  # noqa: S608
    row = connection.execute(select_sql, (user_id,)).fetchone()
    if row is None:
        raise ProfileNotFoundError(f"no profile stored for user {user_id}.")
    return _row_to_profile(row)


def delete_user(connection: sqlite3.Connection, user_id: int, *, actor: str) -> bool:
    """Delete a user and all their private rows; returns ``False`` if absent."""

    with db.transaction(connection):
        cursor = connection.execute("DELETE FROM users WHERE id = ?", (user_id,))
        deleted = cursor.rowcount > 0
        if deleted:
            db.record_audit_event(
                connection,
                actor=actor,
                action="user.deleted",
                outcome="success",
                target_type="user",
                target_id=str(user_id),
            )
    return deleted


def add_profile_fact(
    connection: sqlite3.Connection,
    *,
    user_id: int,
    field_name: str,
    value: str,
    source: str,
    status: str = "pending",
    confidence: float | None = None,
) -> int:
    """Record an extracted fact awaiting confirmation.

    Only facts with ``status == "confirmed"`` may ever be used for outreach;
    Phase 2b wires the confirmation UI. Duplicate (field, value, source) triples
    are ignored so re-parsing a document is idempotent.
    """

    if status not in {"pending", "confirmed", "rejected"}:
        raise ProfileError(f"status must be pending, confirmed or rejected; got {status!r}.")
    field_clean = _clean_required_text(field_name, "field_name")
    value_clean = _clean_required_text(value, "value")
    source_clean = _clean_required_text(source, "source")
    if confidence is not None and not 0.0 <= confidence <= 1.0:
        raise ProfileError(f"confidence must be between 0 and 1; got {confidence!r}.")

    cursor = connection.execute(
        "INSERT INTO profile_facts "
        "(user_id, field, value, source, status, confidence, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (user_id, field, value, source) DO NOTHING",
        (user_id, field_clean, value_clean, source_clean, status, confidence, db.utc_now()),
    )
    if cursor.rowcount == 0:
        row = connection.execute(
            "SELECT id FROM profile_facts "
            "WHERE user_id = ? AND field = ? AND value = ? AND source = ?",
            (user_id, field_clean, value_clean, source_clean),
        ).fetchone()
        return int(row["id"])
    return int(cursor.lastrowid or 0)


def list_profile_facts(
    connection: sqlite3.Connection,
    user_id: int,
    *,
    status: str | None = None,
) -> tuple[Mapping[str, Any], ...]:
    """Return a user's facts, optionally filtered by confirmation status."""

    query = (
        "SELECT id, field, value, source, status, confidence, created_at "
        "FROM profile_facts WHERE user_id = ?"
    )
    params: list[Any] = [user_id]
    if status is not None:
        if status not in {"pending", "confirmed", "rejected"}:
            raise ProfileError(f"status must be pending, confirmed or rejected; got {status!r}.")
        query += " AND status = ?"
        params.append(status)
    query += " ORDER BY id"
    return tuple(dict(row) for row in connection.execute(query, params))


def set_fact_status(
    connection: sqlite3.Connection, *, fact_id: int, user_id: int, status: str
) -> bool:
    """Confirm or reject one fact, scoped to its owner."""

    if status not in {"confirmed", "rejected"}:
        raise ProfileError(f"status must be confirmed or rejected; got {status!r}.")
    cursor = connection.execute(
        "UPDATE profile_facts SET status = ? WHERE id = ? AND user_id = ?",
        (status, fact_id, user_id),
    )
    return cursor.rowcount > 0


def confirmed_facts(connection: sqlite3.Connection, user_id: int) -> tuple[Mapping[str, Any], ...]:
    """Facts safe to use in outbound email (confirmed only)."""

    return list_profile_facts(connection, user_id, status="confirmed")
