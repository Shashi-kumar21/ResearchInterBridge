"""Shared research cache: canonical keys, TTL policy and provenance.

Phase 4 of ``task.md`` (§13, ADR-007). Rules encoded here:

* The cache holds **public research data only** (institutions, departments,
  programs, and later faculty and publications). No student row ever lives here,
  so a second student can reuse a record without seeing the first one's profile.
* Every entry keeps its source URL, fetch/verify timestamps, adapter name,
  parser version, TTL and content hash. A record is never stored without
  provenance.
* TTLs come from the ``task.md`` §13.3 table. A stale entry is *reported* as
  stale, never silently treated as verified, and a failed refresh keeps the
  previous data instead of deleting or guessing.
* ``raw_payload_path`` points at a file outside Git; this module never writes
  payload bytes itself.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from . import db
from .errors import CacheError

DAY_SECONDS = 24 * 60 * 60

#: SQLite caps the number of bound parameters per statement, so batch reads of
#: cache entries are split into groups of this size.
READ_BATCH_SIZE = 500

#: ``task.md`` §13.3. Program data is split three ways because a deadline makes
#: an otherwise slow-changing page time-critical.
TTL_INSTITUTION_SECONDS = 90 * DAY_SECONDS
TTL_DEPARTMENT_SECONDS = 90 * DAY_SECONDS
TTL_FACULTY_SECONDS = 30 * DAY_SECONDS
TTL_PUBLICATION_SECONDS = 30 * DAY_SECONDS
TTL_PROGRAM_SECONDS = 14 * DAY_SECONDS
TTL_PROGRAM_IN_WINDOW_SECONDS = 3 * DAY_SECONDS
TTL_PROGRAM_NEAR_DEADLINE_SECONDS = 1 * DAY_SECONDS

#: A closing date this close counts as "near a deadline": status is checked daily.
NEAR_DEADLINE_DAYS = 7

ENTITY_TYPES: frozenset[str] = frozenset(
    {"institution", "department", "program", "faculty", "publication"}
)

_SLUG_SEPARATOR_RE = re.compile(r"[^\w]+", re.UNICODE)


@dataclass(frozen=True, slots=True)
class CacheEntry:
    """One cached record plus everything needed to judge its freshness."""

    id: int
    entity_type: str
    entity_id: int
    canonical_key: str
    source_url: str
    fetched_at: str
    verified_at: str | None
    ttl_seconds: int
    content_hash: str
    http_status: int | None
    adapter: str
    parser_version: str
    raw_payload_path: str | None
    last_attempt_at: str
    last_error: str | None

    @property
    def checked_at(self) -> datetime:
        """When the stored content was last confirmed against its source."""

        return parse_timestamp(self.verified_at or self.fetched_at)

    @property
    def expires_at(self) -> datetime:
        return self.checked_at + timedelta(seconds=self.ttl_seconds)

    def is_stale(self, now: datetime) -> bool:
        """True when the entry must not be presented as verified.

        A recorded refresh failure marks the entry stale even before its TTL
        runs out: the data is only as fresh as the last successful check.
        """

        return self.last_error is not None or now >= self.expires_at


def parse_timestamp(value: str) -> datetime:
    """Parse a stored ISO-8601 timestamp, normalised to UTC."""

    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def to_timestamp(value: datetime) -> str:
    """Serialise a timestamp the same way :func:`db.utc_now` does."""

    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return value.astimezone(UTC).isoformat(timespec="microseconds")


def normalise_text(value: str) -> str:
    """Collapse whitespace and case-fold a query or name for comparison."""

    return " ".join(value.split()).casefold()


def slug(value: str) -> str:
    """Turn a name into a stable, readable key segment.

    Unicode word characters are kept, so non-Latin institution names do not
    collapse to an empty key.
    """

    cleaned = _SLUG_SEPARATOR_RE.sub("-", normalise_text(value)).strip("-")
    return cleaned or "unknown"


def canonical_key(*parts: str) -> str:
    """Build a readable canonical key such as ``program:de:tu-munich:physics:msc``."""

    segments = [slug(part) for part in parts if part and part.strip()]
    if not segments:
        raise CacheError("a canonical key needs at least one non-empty part.")
    return ":".join(segments)


def content_hash(payload: Mapping[str, Any]) -> str:
    """Deterministic SHA-256 of a record, used to detect changed content."""

    encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def ttl_seconds(
    entity_type: str,
    *,
    opening_date: str | None = None,
    closing_date: str | None = None,
    now: datetime | None = None,
) -> int:
    """Return the §13.3 TTL for one entity type.

    Program pages depend on the application window: three days while a window is
    open and one day near (or past) a closing date, so a closed status is never
    served from an old cache entry. Unknown dates are not invented; they simply
    keep the slower default.
    """

    if entity_type == "institution":
        return TTL_INSTITUTION_SECONDS
    if entity_type == "department":
        return TTL_DEPARTMENT_SECONDS
    if entity_type == "faculty":
        return TTL_FACULTY_SECONDS
    if entity_type == "publication":
        return TTL_PUBLICATION_SECONDS
    if entity_type != "program":
        raise CacheError(
            f"unknown cache entity type {entity_type!r}; expected one of "
            f"{', '.join(sorted(ENTITY_TYPES))}."
        )

    today = (now or datetime.now(UTC)).astimezone(UTC).date()
    closing = _optional_date(closing_date)
    opening = _optional_date(opening_date)
    if closing is not None and today >= closing - timedelta(days=NEAR_DEADLINE_DAYS):
        return TTL_PROGRAM_NEAR_DEADLINE_SECONDS
    if opening is not None and closing is not None and opening <= today <= closing:
        return TTL_PROGRAM_IN_WINDOW_SECONDS
    return TTL_PROGRAM_SECONDS


def _optional_date(value: str | None) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise CacheError(f"{value!r} is not an ISO-8601 date (YYYY-MM-DD).") from exc


def put_entry(
    connection: sqlite3.Connection,
    *,
    entity_type: str,
    entity_id: int,
    canonical_key: str,
    source_url: str,
    fetched_at: str,
    verified_at: str | None,
    ttl_seconds: int,
    content_hash: str,
    http_status: int | None,
    adapter: str,
    parser_version: str,
    raw_payload_path: str | None = None,
) -> int:
    """Insert or refresh one cache entry. Returns its id.

    The caller owns the transaction, so a program row, its sources and its cache
    entry become visible together. A successful refresh clears ``last_error``.
    """

    if entity_type not in ENTITY_TYPES:
        raise CacheError(f"unknown cache entity type {entity_type!r}.")
    if ttl_seconds <= 0:
        raise CacheError("a cache TTL must be positive.")
    row = connection.execute(
        """
        INSERT INTO research_cache_entries (
            entity_type, entity_id, canonical_key, source_url, fetched_at,
            verified_at, ttl_seconds, content_hash, http_status, adapter,
            parser_version, raw_payload_path, last_attempt_at, last_error
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL)
        ON CONFLICT(canonical_key) DO UPDATE SET
            entity_type = excluded.entity_type,
            entity_id = excluded.entity_id,
            source_url = excluded.source_url,
            fetched_at = excluded.fetched_at,
            verified_at = excluded.verified_at,
            ttl_seconds = excluded.ttl_seconds,
            content_hash = excluded.content_hash,
            http_status = excluded.http_status,
            adapter = excluded.adapter,
            parser_version = excluded.parser_version,
            raw_payload_path = excluded.raw_payload_path,
            last_attempt_at = excluded.last_attempt_at,
            last_error = NULL
        RETURNING id
        """,
        (
            entity_type,
            entity_id,
            canonical_key,
            source_url,
            fetched_at,
            verified_at,
            ttl_seconds,
            content_hash,
            http_status,
            adapter,
            parser_version,
            raw_payload_path,
            fetched_at,
        ),
    ).fetchone()
    return int(row["id"])


def get_entry(connection: sqlite3.Connection, canonical_key: str) -> CacheEntry | None:
    """Return one cache entry by canonical key, or ``None``."""

    row = connection.execute(
        f"SELECT {_COLUMNS} FROM research_cache_entries WHERE canonical_key = ?",  # noqa: S608
        (canonical_key,),
    ).fetchone()
    return None if row is None else _entry_from_row(row)


def entries_for_entity(
    connection: sqlite3.Connection, *, entity_type: str, entity_id: int
) -> tuple[CacheEntry, ...]:
    """Return every cache row pointing at one entity (normally exactly one)."""

    rows = connection.execute(
        f"SELECT {_COLUMNS} FROM research_cache_entries "  # noqa: S608
        "WHERE entity_type = ? AND entity_id = ? ORDER BY id",
        (entity_type, entity_id),
    ).fetchall()
    return tuple(_entry_from_row(row) for row in rows)


def entries_for_entities(
    connection: sqlite3.Connection, *, entity_type: str, entity_ids: Sequence[int]
) -> dict[int, tuple[CacheEntry, ...]]:
    """Batch form of :func:`entries_for_entity`, keyed by entity id.

    Used when listing programs so the screen does not issue one query per row.
    Large id lists are split because SQLite caps bound parameters per statement.
    """

    if not entity_ids:
        return {}
    grouped: dict[int, list[CacheEntry]] = {}
    unique_ids = tuple(dict.fromkeys(entity_ids))
    for start in range(0, len(unique_ids), READ_BATCH_SIZE):
        batch = unique_ids[start : start + READ_BATCH_SIZE]
        placeholders = ", ".join("?" for _ in batch)
        rows = connection.execute(
            f"SELECT {_COLUMNS} FROM research_cache_entries "  # noqa: S608
            f"WHERE entity_type = ? AND entity_id IN ({placeholders}) ORDER BY id",
            (entity_type, *batch),
        ).fetchall()
        for row in rows:
            entry = _entry_from_row(row)
            grouped.setdefault(entry.entity_id, []).append(entry)
    return {entity_id: tuple(values) for entity_id, values in grouped.items()}


def mark_refresh_failed(
    connection: sqlite3.Connection,
    *,
    entity_type: str,
    entity_id: int,
    attempted_at: str,
    error: str,
) -> int:
    """Record a failed refresh without touching the stored data.

    ``error`` is a short class name or reason, never a provider payload. Returns
    the number of entries marked.
    """

    cursor = connection.execute(
        "UPDATE research_cache_entries SET last_attempt_at = ?, last_error = ? "
        "WHERE entity_type = ? AND entity_id = ?",
        (attempted_at, error, entity_type, entity_id),
    )
    return cursor.rowcount


def list_entries(
    connection: sqlite3.Connection, *, entity_type: str | None = None
) -> tuple[CacheEntry, ...]:
    """List cache entries, optionally filtered by entity type."""

    if entity_type is None:
        rows = connection.execute(
            f"SELECT {_COLUMNS} FROM research_cache_entries ORDER BY id"  # noqa: S608
        ).fetchall()
    else:
        rows = connection.execute(
            f"SELECT {_COLUMNS} FROM research_cache_entries "  # noqa: S608
            "WHERE entity_type = ? ORDER BY id",
            (entity_type,),
        ).fetchall()
    return tuple(_entry_from_row(row) for row in rows)


_COLUMNS = (
    "id, entity_type, entity_id, canonical_key, source_url, fetched_at, verified_at, "
    "ttl_seconds, content_hash, http_status, adapter, parser_version, raw_payload_path, "
    "last_attempt_at, last_error"
)


def _entry_from_row(row: sqlite3.Row) -> CacheEntry:
    return CacheEntry(
        id=int(row["id"]),
        entity_type=str(row["entity_type"]),
        entity_id=int(row["entity_id"]),
        canonical_key=str(row["canonical_key"]),
        source_url=str(row["source_url"]),
        fetched_at=str(row["fetched_at"]),
        verified_at=None if row["verified_at"] is None else str(row["verified_at"]),
        ttl_seconds=int(row["ttl_seconds"]),
        content_hash=str(row["content_hash"]),
        http_status=None if row["http_status"] is None else int(row["http_status"]),
        adapter=str(row["adapter"]),
        parser_version=str(row["parser_version"]),
        raw_payload_path=(
            None if row["raw_payload_path"] is None else str(row["raw_payload_path"])
        ),
        last_attempt_at=str(row["last_attempt_at"]),
        last_error=None if row["last_error"] is None else str(row["last_error"]),
    )


def utc_now() -> str:
    """The cache's timestamp format; delegates so tests can patch one place."""

    return db.utc_now()
