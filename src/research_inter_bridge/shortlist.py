"""Per-user shortlist with explained score snapshots (Phase 6b, ADR-022).

Rules enforced here:

* **Private.** Every row carries the caller's ``user_id`` and every read and
  write is scoped to it (``task.md`` §13.4). A student can never see, change or
  learn about another student's shortlist, not even by guessing a target id.
* **Deduplicated.** One row per (student, target). Shortlisting a target twice
  is a no-op; shortlisting an excluded target moves it back.
* **Snapshots, not live scores.** A score is stored only when a target is
  shortlisted or a refresh finds its inputs or result changed. A snapshot says
  whether it is still current.
* **Atomic multi-select.** Adding several targets is one transaction: every
  target is checked first, so an unknown id adds nothing.
* Audit rows name counts and public cache ids only, never profile content.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from typing import Literal

from . import db, discovery, matching, profiles
from . import faculty as faculty_service
from .errors import (
    FacultyNotFoundError,
    ProgramNotFoundError,
    ShortlistError,
    ShortlistItemNotFoundError,
)

TargetType = Literal["faculty", "program"]
ItemStatus = Literal["shortlisted", "excluded"]

TARGET_TYPES: frozenset[str] = frozenset({"faculty", "program"})

#: One multi-select request; keeps a single transaction small.
MAX_BATCH = 50
#: Rows per student, shortlisted and excluded together.
MAX_ITEMS = 500


@dataclass(frozen=True, slots=True)
class Target:
    type: str
    id: int

    def __post_init__(self) -> None:
        if self.type not in TARGET_TYPES:
            raise ShortlistError(
                f"target type {self.type!r} is not supported; use 'faculty' or 'program'."
            )
        if isinstance(self.id, bool) or not isinstance(self.id, int) or self.id <= 0:
            raise ShortlistError("target id must be a positive integer.")


@dataclass(frozen=True, slots=True)
class Snapshot:
    id: int
    score: int
    eligibility: str
    scoring_version: str
    inputs_fingerprint: str
    evaluated_on: str
    #: The stored factors, gates and gaps, as :func:`matching` produced them.
    result: dict[str, object]
    created_at: str


@dataclass(frozen=True, slots=True)
class ShortlistEntry:
    id: int
    target_type: str
    target_id: int
    #: ``None`` when the cached record no longer exists.
    target_name: str | None
    status: str
    snapshot: Snapshot | None
    #: True when re-scoring today gives the same inputs, score and eligibility.
    #: ``None`` when there is nothing to compare (no snapshot or no target).
    current: bool | None
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class ChangeResult:
    changed: tuple[Target, ...]
    unchanged: tuple[Target, ...]


# --------------------------------------------------------------------------- #
# writes
# --------------------------------------------------------------------------- #


def add(
    connection: sqlite3.Connection,
    user_id: int,
    targets: Sequence[Target],
    *,
    today: date | None = None,
    weights: matching.MatchWeights = matching.DEFAULT_WEIGHTS,
) -> ChangeResult:
    """Shortlist several targets at once and snapshot their explained scores.

    Raises before writing anything when the profile is missing or any target is
    not in the shared cache.
    """

    day = today or datetime.now(UTC).date()
    unique = _unique(targets)
    profile = profiles.load_profile(connection, user_id)
    scored = {target: _score(connection, profile, target, day, weights) for target in unique}
    changed: list[Target] = []
    unchanged: list[Target] = []
    with db.transaction(connection):
        _check_capacity(connection, user_id, unique)
        for target in unique:
            existing = _item_row(connection, user_id, target)
            if existing is not None and existing["status"] == "shortlisted":
                unchanged.append(target)
                continue
            item_id = _upsert(connection, user_id, target, "shortlisted", existing)
            _insert_snapshot(connection, user_id, item_id, scored[target][0])
            changed.append(target)
        _audit(connection, user_id, "shortlist.add", changed, unchanged)
    return ChangeResult(tuple(changed), tuple(unchanged))


def exclude(
    connection: sqlite3.Connection,
    user_id: int,
    targets: Sequence[Target],
    *,
    now: datetime | None = None,
) -> ChangeResult:
    """Remember that the student does not want these targets suggested."""

    unique = _unique(targets)
    checked_at = now or datetime.now(UTC)
    for target in unique:
        _require_target(connection, target, checked_at)
    changed: list[Target] = []
    unchanged: list[Target] = []
    with db.transaction(connection):
        _check_capacity(connection, user_id, unique)
        for target in unique:
            existing = _item_row(connection, user_id, target)
            if existing is not None and existing["status"] == "excluded":
                unchanged.append(target)
                continue
            _upsert(connection, user_id, target, "excluded", existing)
            changed.append(target)
        _audit(connection, user_id, "shortlist.exclude", changed, unchanged)
    return ChangeResult(tuple(changed), tuple(unchanged))


def remove(connection: sqlite3.Connection, user_id: int, target: Target) -> None:
    """Forget a target entirely; its snapshots go with it."""

    with db.transaction(connection):
        cursor = connection.execute(
            "DELETE FROM shortlist_items WHERE user_id = ? AND target_type = ? AND target_id = ?",
            (user_id, target.type, target.id),
        )
        if cursor.rowcount == 0:
            raise ShortlistItemNotFoundError(
                f"{target.type} {target.id} is not on this student's shortlist."
            )
        _audit(connection, user_id, "shortlist.remove", [target], [])


def refresh(
    connection: sqlite3.Connection,
    user_id: int,
    *,
    today: date | None = None,
    weights: matching.MatchWeights = matching.DEFAULT_WEIGHTS,
) -> ChangeResult:
    """Re-score shortlisted targets; store a snapshot only where something changed."""

    day = today or datetime.now(UTC).date()
    profile = profiles.load_profile(connection, user_id)
    rows = connection.execute(
        "SELECT id, target_type, target_id FROM shortlist_items "
        "WHERE user_id = ? AND status = 'shortlisted' ORDER BY id",
        (user_id,),
    ).fetchall()
    changed: list[Target] = []
    unchanged: list[Target] = []
    with db.transaction(connection):
        for row in rows:
            target = Target(str(row["target_type"]), int(row["target_id"]))
            try:
                result, _ = _score(connection, profile, target, day, weights)
            except (FacultyNotFoundError, ProgramNotFoundError):
                unchanged.append(target)
                continue
            latest = _latest_snapshot(connection, int(row["id"]))
            if latest is not None and _same(latest, result):
                unchanged.append(target)
                continue
            _insert_snapshot(connection, user_id, int(row["id"]), result)
            changed.append(target)
        _audit(connection, user_id, "shortlist.refresh", changed, unchanged)
    return ChangeResult(tuple(changed), tuple(unchanged))


# --------------------------------------------------------------------------- #
# reads
# --------------------------------------------------------------------------- #


def list_items(
    connection: sqlite3.Connection,
    user_id: int,
    *,
    status: ItemStatus | None = None,
    today: date | None = None,
    weights: matching.MatchWeights = matching.DEFAULT_WEIGHTS,
) -> tuple[ShortlistEntry, ...]:
    """The student's rows, best snapshot first; ineligible ones after the rest."""

    if status is not None and status not in ("shortlisted", "excluded"):
        raise ShortlistError("status must be 'shortlisted' or 'excluded'.")
    day = today or datetime.now(UTC).date()
    try:
        profile: profiles.StudentProfile | None = profiles.load_profile(connection, user_id)
    except profiles.ProfileNotFoundError:
        profile = None
    where = "user_id = ?" + ("" if status is None else " AND status = ?")
    parameters: tuple[object, ...] = (user_id,) if status is None else (user_id, status)
    rows = connection.execute(
        f"SELECT * FROM shortlist_items WHERE {where} ORDER BY id",  # noqa: S608
        parameters,
    ).fetchall()
    entries: list[ShortlistEntry] = []
    for row in rows:
        target = Target(str(row["target_type"]), int(row["target_id"]))
        snapshot = _latest_snapshot(connection, int(row["id"]))
        name: str | None
        current: bool | None = None
        try:
            if profile is not None:
                result, name = _score(connection, profile, target, day, weights)
                current = None if snapshot is None else _same(snapshot, result)
            else:
                name = _target_name(connection, target)
        except (FacultyNotFoundError, ProgramNotFoundError):
            name = None
        entries.append(
            ShortlistEntry(
                id=int(row["id"]),
                target_type=target.type,
                target_id=target.id,
                target_name=name,
                status=str(row["status"]),
                snapshot=snapshot,
                current=current,
                created_at=str(row["created_at"]),
                updated_at=str(row["updated_at"]),
            )
        )
    return tuple(sorted(entries, key=_entry_order))


def statuses(connection: sqlite3.Connection, user_id: int, target_type: str) -> dict[int, str]:
    """Target id -> ``shortlisted``/``excluded`` for one student and target type."""

    return {
        int(row["target_id"]): str(row["status"])
        for row in connection.execute(
            "SELECT target_id, status FROM shortlist_items WHERE user_id = ? AND target_type = ?",
            (user_id, target_type),
        )
    }


def excluded_targets(connection: sqlite3.Connection, user_id: int) -> frozenset[Target]:
    """Targets a browse screen should hide for this student."""

    return frozenset(
        Target(str(row["target_type"]), int(row["target_id"]))
        for row in connection.execute(
            "SELECT target_type, target_id FROM shortlist_items "
            "WHERE user_id = ? AND status = 'excluded'",
            (user_id,),
        )
    )


def export_payload(connection: sqlite3.Connection, user_id: int) -> list[dict[str, object]]:
    """The student's shortlist for the account data export."""

    return [
        {
            "target_type": entry.target_type,
            "target_id": entry.target_id,
            "status": entry.status,
            "created_at": entry.created_at,
            "score": None if entry.snapshot is None else entry.snapshot.score,
            "eligibility": None if entry.snapshot is None else entry.snapshot.eligibility,
            "evaluated_on": None if entry.snapshot is None else entry.snapshot.evaluated_on,
        }
        for entry in list_items(connection, user_id)
    ]


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #


def _unique(targets: Sequence[Target]) -> tuple[Target, ...]:
    if not targets:
        raise ShortlistError("select at least one faculty member or program.")
    unique = tuple(dict.fromkeys(targets))
    if len(unique) > MAX_BATCH:
        raise ShortlistError(f"select at most {MAX_BATCH} targets at a time.")
    return unique


def _check_capacity(
    connection: sqlite3.Connection, user_id: int, targets: Sequence[Target]
) -> None:
    existing = {
        Target(str(row["target_type"]), int(row["target_id"]))
        for row in connection.execute(
            "SELECT target_type, target_id FROM shortlist_items WHERE user_id = ?", (user_id,)
        )
    }
    if len(existing | set(targets)) > MAX_ITEMS:
        raise ShortlistError(
            f"a shortlist holds at most {MAX_ITEMS} rows; remove some before adding more.",
            code="shortlist_full",
        )


def _require_target(connection: sqlite3.Connection, target: Target, now: datetime) -> None:
    if target.type == "faculty":
        faculty_service.get_faculty(connection, target.id, now=now)
    else:
        discovery.get_program(connection, target.id, now=now)


def _target_name(connection: sqlite3.Connection, target: Target) -> str:
    if target.type == "faculty":
        return faculty_service.get_faculty(connection, target.id).full_name
    return discovery.get_program(connection, target.id).name


def _score(
    connection: sqlite3.Connection,
    profile: profiles.StudentProfile,
    target: Target,
    day: date,
    weights: matching.MatchWeights,
) -> tuple[matching.MatchResult, str]:
    now = datetime.combine(day, datetime.min.time(), tzinfo=UTC)
    if target.type == "faculty":
        member = faculty_service.get_faculty(connection, target.id, now=now)
        return matching.score_faculty(profile, member, today=day, weights=weights), member.full_name
    program = discovery.get_program(connection, target.id, now=now)
    return matching.score_program(profile, program, today=day, weights=weights), program.name


def _item_row(connection: sqlite3.Connection, user_id: int, target: Target) -> sqlite3.Row | None:
    return connection.execute(
        "SELECT id, status FROM shortlist_items "
        "WHERE user_id = ? AND target_type = ? AND target_id = ?",
        (user_id, target.type, target.id),
    ).fetchone()


def _upsert(
    connection: sqlite3.Connection,
    user_id: int,
    target: Target,
    status: str,
    existing: sqlite3.Row | None,
) -> int:
    stamp = db.utc_now()
    if existing is not None:
        connection.execute(
            "UPDATE shortlist_items SET status = ?, updated_at = ? WHERE id = ?",
            (status, stamp, int(existing["id"])),
        )
        return int(existing["id"])
    cursor = connection.execute(
        "INSERT INTO shortlist_items "
        "(user_id, target_type, target_id, status, created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (user_id, target.type, target.id, status, stamp, stamp),
    )
    return int(cursor.lastrowid or 0)


def _result_payload(result: matching.MatchResult) -> dict[str, object]:
    return {
        "target_name": result.target_name,
        "gates": [asdict(gate) for gate in result.gates],
        "factors": [{**asdict(factor), "points": factor.points} for factor in result.factors],
        "gaps": list(result.gaps),
    }


def _insert_snapshot(
    connection: sqlite3.Connection, user_id: int, item_id: int, result: matching.MatchResult
) -> None:
    connection.execute(
        "INSERT INTO match_scores (user_id, shortlist_item_id, score, eligibility, "
        "scoring_version, inputs_fingerprint, evaluated_on, result_json, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (
            user_id,
            item_id,
            result.score,
            result.eligibility,
            result.scoring_version,
            result.inputs_fingerprint,
            result.evaluated_on,
            json.dumps(_result_payload(result), sort_keys=True, ensure_ascii=False),
            db.utc_now(),
        ),
    )


def _latest_snapshot(connection: sqlite3.Connection, item_id: int) -> Snapshot | None:
    row = connection.execute(
        "SELECT * FROM match_scores WHERE shortlist_item_id = ? ORDER BY id DESC LIMIT 1",
        (item_id,),
    ).fetchone()
    if row is None:
        return None
    return Snapshot(
        id=int(row["id"]),
        score=int(row["score"]),
        eligibility=str(row["eligibility"]),
        scoring_version=str(row["scoring_version"]),
        inputs_fingerprint=str(row["inputs_fingerprint"]),
        evaluated_on=str(row["evaluated_on"]),
        result=json.loads(row["result_json"]),
        created_at=str(row["created_at"]),
    )


def _same(snapshot: Snapshot, result: matching.MatchResult) -> bool:
    return (
        snapshot.inputs_fingerprint == result.inputs_fingerprint
        and snapshot.score == result.score
        and snapshot.eligibility == result.eligibility
    )


def _entry_order(entry: ShortlistEntry) -> tuple[int, int, int, str, int]:
    snapshot = entry.snapshot
    return (
        0 if entry.status == "shortlisted" else 1,
        1 if snapshot is not None and snapshot.eligibility == "ineligible" else 0,
        -(snapshot.score if snapshot is not None else -1),
        (entry.target_name or "").casefold(),
        entry.id,
    )


def _audit(
    connection: sqlite3.Connection,
    user_id: int,
    action: str,
    changed: Sequence[Target],
    unchanged: Sequence[Target],
) -> None:
    db.record_audit_event(
        connection,
        actor=f"user:{user_id}",
        action=action,
        outcome="success",
        target_type="shortlist",
        target_id=str(user_id),
        metadata={
            "changed": [f"{target.type}:{target.id}" for target in changed],
            "unchanged": len(unchanged),
        },
    )
