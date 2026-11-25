"""Cross-user reuse of cached candidate sets (task.md §13.4, ADR-023).

Rules enforced here:

* **Derived, non-identifying features only.** A bucket is the OECD field set of
  the student's research keywords (:mod:`taxonomy`), their degree level and
  their target countries. No name, email, free text, GPA or document ever
  enters it, and only a hash of it is stored.
* **k >= 3.** A result set is reused only when at least :data:`MIN_PROFILES`
  distinct students fall in the same bucket; otherwise matching computes
  normally. A student whose keywords map only to ``other`` has no bucket and
  never reuses.
* **Ids only.** A stored result set holds candidate ids of public cache records,
  chosen from the bucket's fields and the query filters, never from any one
  student's profile. It holds no score, gate or snapshot; each student
  re-scores the candidates with their own profile.
* **Audited.** Every reuse writes an audit event with the query fingerprint, the
  reused ids, the bucket id and the member count. No personal content.
* **Cheap.** No background job and no paid service: a set is built lazily on the
  first qualifying request and expires after :data:`RESULT_SET_TTL`.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass, is_dataclass
from datetime import datetime, timedelta
from typing import TypeVar

from . import db, taxonomy
from .matching import _STUDENT_LEVELS, _phrases
from .profiles import StudentProfile

#: Distinct students a bucket needs before its result sets are reused.
MIN_PROFILES = 3
#: How long a stored candidate set is reused before it is rebuilt.
RESULT_SET_TTL = timedelta(hours=6)

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class Bucket:
    id: str
    fields: tuple[str, ...]
    degree_level: str
    countries: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CandidateSet:
    ids: tuple[int, ...]
    truncated: bool
    #: True when the set came from storage (a reuse), False when just built.
    reused: bool
    #: The records already loaded while building, in ``ids`` order; empty on a
    #: reuse, so the caller loads exactly the ids and nothing else.
    built: tuple[object, ...] = ()


def bucket_for(profile: StudentProfile) -> Bucket | None:
    """The student's bucket, or ``None`` when they have no mapped field."""

    fields = tuple(sorted(taxonomy.fields_for(profile.research_keywords) - {taxonomy.OTHER}))
    if not fields:
        return None
    levels = (
        "unknown"
        if profile.degree_type is None or profile.degree_type not in _STUDENT_LEVELS
        else "+".join(sorted(_STUDENT_LEVELS[profile.degree_type]))
    )
    countries = tuple(sorted(_phrases(profile.target_countries)))
    digest = hashlib.sha256(
        json.dumps({"fields": fields, "degree": levels, "countries": countries}).encode("utf-8")
    ).hexdigest()[:16]
    return Bucket(id=digest, fields=fields, degree_level=levels, countries=countries)


def record_membership(connection: sqlite3.Connection, user_id: int, bucket: Bucket | None) -> None:
    """Keep the student's single bucket row current; drop it when they have none."""

    if bucket is None:
        connection.execute("DELETE FROM profile_buckets WHERE user_id = ?", (user_id,))
        return
    connection.execute(
        "INSERT INTO profile_buckets (user_id, bucket_id, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT (user_id) DO UPDATE SET bucket_id = excluded.bucket_id, "
        "updated_at = excluded.updated_at",
        (user_id, bucket.id, db.utc_now()),
    )


def member_count(connection: sqlite3.Connection, bucket_id: str) -> int:
    row = connection.execute(
        "SELECT COUNT(DISTINCT user_id) FROM profile_buckets WHERE bucket_id = ?", (bucket_id,)
    ).fetchone()
    return int(row[0])


def query_fingerprint(target_type: str, filters: object) -> str:
    payload = {
        "type": target_type,
        "filters": asdict(filters) if is_dataclass(filters) else None,  # type: ignore[arg-type]
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def candidates(
    connection: sqlite3.Connection,
    bucket: Bucket,
    target_type: str,
    filters: object,
    *,
    members: int,
    collect: Callable[[], tuple[list[T], bool]],
    areas_of: Callable[[T], Sequence[str]],
    id_of: Callable[[T], int],
    now: datetime,
    cap: int,
) -> CandidateSet:
    """The bucket's candidate ids for this query: reused when stored, else built.

    Building reads the cache through ``collect`` and keeps records whose stated
    areas share an OECD field with the bucket, most shared fields first. It uses
    the bucket's fields only, so the set says nothing about any one student.
    """

    fingerprint = query_fingerprint(target_type, filters)
    stamp = now.isoformat()
    row = connection.execute(
        "SELECT candidate_ids_json, truncated FROM bucket_result_sets "
        "WHERE bucket_id = ? AND target_type = ? AND query_fingerprint = ? AND expires_at > ?",
        (bucket.id, target_type, fingerprint, stamp),
    ).fetchone()
    if row is not None:
        ids = tuple(int(value) for value in json.loads(row["candidate_ids_json"]))
        db.record_audit_event(
            connection,
            actor="system",
            action="match.result_reuse",
            outcome="success",
            target_type="bucket",
            target_id=bucket.id,
            metadata={
                "query_fingerprint": fingerprint,
                "target_type": target_type,
                "reused_ids": list(ids),
                "bucket_members": members,
            },
        )
        return CandidateSet(ids=ids, truncated=bool(row["truncated"]), reused=True)

    records, truncated = collect()
    wanted = set(bucket.fields)
    ranked: list[tuple[int, int]] = []
    for record in records:
        overlap = len(taxonomy.fields_for(areas_of(record)) & wanted)
        if overlap:
            ranked.append((-overlap, id_of(record)))
    ids = tuple(record_id for _, record_id in sorted(ranked)[:cap])
    by_id = {id_of(record): record for record in records}
    connection.execute(
        "INSERT INTO bucket_result_sets (bucket_id, target_type, query_fingerprint, "
        "candidate_ids_json, truncated, created_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT (bucket_id, target_type, query_fingerprint) DO UPDATE SET "
        "candidate_ids_json = excluded.candidate_ids_json, truncated = excluded.truncated, "
        "created_at = excluded.created_at, expires_at = excluded.expires_at",
        (
            bucket.id,
            target_type,
            fingerprint,
            json.dumps(list(ids)),
            int(truncated),
            stamp,
            (now + RESULT_SET_TTL).isoformat(),
        ),
    )
    return CandidateSet(
        ids=ids, truncated=truncated, reused=False, built=tuple(by_id[i] for i in ids)
    )


def export_payload(connection: sqlite3.Connection, user_id: int) -> dict[str, object] | None:
    """The student's own bucket row for the account export (an id, no features)."""

    row = connection.execute(
        "SELECT bucket_id, updated_at FROM profile_buckets WHERE user_id = ?", (user_id,)
    ).fetchone()
    if row is None:
        return None
    return {"bucket_id": str(row["bucket_id"]), "updated_at": str(row["updated_at"])}
