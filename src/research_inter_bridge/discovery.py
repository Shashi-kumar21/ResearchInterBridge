"""Program discovery over the shared research cache.

Phase 4 of ``task.md`` (§3.4, §13). Discovery is deliberately source-agnostic:

* An adapter turns one official source into :class:`DiscoveredProgram` records.
  It must not invent a deadline, stipend or eligibility rule: an unknown value
  stays ``None``/empty, and a page that was not checked keeps ``unverified``.
* This module validates those records, stores them together with their
  provenance (source URL, adapter, parser version, fetch time, content hash) and
  serves them from SQLite. A repeated query must not reach the adapter while the
  cache is fresh.
* A refresh failure keeps the previous rows, marks them stale and records the
  failure. It never deletes verified data and never replaces it with a guess.
* ``offline=True`` never calls an adapter, so the browse screens keep working
  without a network (§13.2.5).

Faculty and publications reuse the same cache tables and TTLs in Phase 5; this
module currently models programs only.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime, timedelta
from typing import Any, Protocol
from urllib.parse import urlsplit

from . import db
from . import research_cache as cache
from .errors import CacheError, DiscoveryError, DiscoveryUnavailableError

PROGRAM_STATUSES: frozenset[str] = frozenset({"open", "upcoming", "closed", "unverified"})

MAX_NAME_LENGTH = 300
MAX_TEXT_LENGTH = 2000
MAX_URL_LENGTH = 2048
MAX_ITEMS = 50

#: An empty result set is cached briefly so a repeated query does not call the
#: source every time. Non-empty result sets follow their program TTLs.
EMPTY_RESULT_TTL_DAYS = 1

#: SQLite limits the number of bound parameters per statement. Reads are split
#: into batches of this size so browsing the whole cache stays safe.
READ_BATCH_SIZE = 500

_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f]")


@dataclass(frozen=True, slots=True)
class ProgramQuery:
    """A normalised program search.

    Every field is optional so an empty query can browse the whole cache. The
    fingerprint is computed over the normalised values only, which makes
    ``"Machine Learning "`` and ``"machine learning"`` the same cache entry.
    """

    research_area: str | None = None
    country: str | None = None
    department: str | None = None
    degree_level: str | None = None

    def normalised(self) -> ProgramQuery:
        return ProgramQuery(
            research_area=_query_text(self.research_area, "research_area", 200),
            country=_query_text(self.country, "country", 200),
            department=_query_text(self.department, "department", 200),
            degree_level=_query_text(self.degree_level, "degree_level", 200),
        )

    def is_empty(self) -> bool:
        return all(
            value is None
            for value in (
                self.research_area,
                self.country,
                self.department,
                self.degree_level,
            )
        )

    def fingerprint(self) -> str:
        normalised = self.normalised()
        payload = {
            "research_area": normalised.research_area,
            "country": normalised.country,
            "department": normalised.department,
            "degree_level": normalised.degree_level,
        }
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        return f"query:{hashlib.sha256(encoded).hexdigest()}"

    def as_dict(self) -> dict[str, str | None]:
        normalised = self.normalised()
        return {
            "research_area": normalised.research_area,
            "country": normalised.country,
            "department": normalised.department,
            "degree_level": normalised.degree_level,
        }


@dataclass(frozen=True, slots=True)
class SourceRef:
    """One page that backs a record. The official page comes first."""

    url: str
    publisher: str | None = None
    kind: str = "official_page"


@dataclass(frozen=True, slots=True)
class DiscoveredProgram:
    """One adapter record, before it is validated and stored.

    Defaults are "unknown", not "no": an unstated deadline is ``None`` and an
    unchecked status is ``unverified``. Only a source that states "not funded"
    may produce text saying so.
    """

    name: str
    institution: str
    official_url: str
    country: str | None = None
    department: str | None = None
    opportunity_type: str | None = None
    research_areas: tuple[str, ...] = ()
    eligibility: tuple[str, ...] = ()
    application_method: str | None = None
    opening_date: str | None = None
    closing_date: str | None = None
    start_date: str | None = None
    duration: str | None = None
    stipend: str | None = None
    tuition: str | None = None
    accommodation: str | None = None
    required_documents: tuple[str, ...] = ()
    status: str = "unverified"
    confidence: float | None = None
    sources: tuple[SourceRef, ...] = ()
    http_status: int | None = None
    raw_payload_path: str | None = None


@dataclass(frozen=True, slots=True)
class Program:
    """A stored program as returned to callers."""

    id: int
    canonical_key: str
    name: str
    institution: str | None
    department: str | None
    country: str | None
    opportunity_type: str | None
    research_areas: tuple[str, ...]
    official_url: str
    evidence_urls: tuple[str, ...]
    eligibility: tuple[str, ...]
    application_method: str | None
    opening_date: str | None
    closing_date: str | None
    start_date: str | None
    duration: str | None
    stipend: str | None
    tuition: str | None
    accommodation: str | None
    required_documents: tuple[str, ...]
    status: str
    confidence: float | None
    last_checked_at: str | None
    stale: bool


@dataclass(frozen=True, slots=True)
class DiscoveryResult:
    """What a query answered, and how trustworthy the answer is right now."""

    query: ProgramQuery
    programs: tuple[Program, ...]
    served_from_cache: bool
    stale: bool
    message: str | None


class ProgramAdapter(Protocol):
    """A source adapter. Implementations perform network I/O only in ``search``.

    ``search`` receives a normalised query (trimmed, case-folded), so an adapter
    never has to redo cache-key normalisation. It must return records with
    provenance and must not fill an unknown value.
    """

    name: str
    parser_version: str

    def search(self, query: ProgramQuery) -> Sequence[DiscoveredProgram]:
        """Return records for ``query``; raise :class:`DiscoveryError` on failure."""
        ...


# --------------------------------------------------------------------------- #
# validation
# --------------------------------------------------------------------------- #


def validate_program(record: DiscoveredProgram) -> DiscoveredProgram:
    """Return a cleaned record or raise :class:`DiscoveryError`.

    This is the single gate between an untrusted provider payload and the
    database. It checks shape and internal consistency only; it never fills a
    missing value.
    """

    if not isinstance(record, DiscoveredProgram):
        raise DiscoveryError("a discovery adapter must return DiscoveredProgram records.")
    name = _required_text(record.name, "name", MAX_NAME_LENGTH)
    institution = _required_text(record.institution, "institution", MAX_NAME_LENGTH)
    official_url = _url(record.official_url, "official_url")
    status = record.status.strip().casefold()
    if status not in PROGRAM_STATUSES:
        raise DiscoveryError(
            f"program {name!r} has status {record.status!r}; expected one of "
            f"{', '.join(sorted(PROGRAM_STATUSES))}."
        )
    opening = _optional_date(record.opening_date, "opening_date")
    closing = _optional_date(record.closing_date, "closing_date")
    start = _optional_date(record.start_date, "start_date")
    if opening is not None and closing is not None and opening > closing:
        raise DiscoveryError(
            f"program {name!r} opens on {opening} but closes on {closing}; "
            "the opening date cannot be after the closing date."
        )
    confidence = _confidence(record.confidence, name)
    if record.http_status is not None and not isinstance(record.http_status, int):
        raise DiscoveryError("http_status must be an integer or None.")
    http_status = record.http_status
    if http_status is not None and not 100 <= http_status <= 599:
        raise DiscoveryError("http_status must be an HTTP status between 100 and 599.")

    sources = tuple(
        SourceRef(
            url=_url(source.url, "source url"),
            publisher=_optional_text(source.publisher, "source publisher", MAX_NAME_LENGTH),
            kind=_required_text(source.kind, "source kind", 50),
        )
        for source in record.sources
    )
    if not any(source.url == official_url for source in sources):
        sources = (SourceRef(url=official_url), *sources)

    return replace(
        record,
        name=name,
        institution=institution,
        official_url=official_url,
        country=_optional_text(record.country, "country", 200),
        department=_optional_text(record.department, "department", MAX_NAME_LENGTH),
        opportunity_type=_optional_text(record.opportunity_type, "opportunity_type", 100),
        research_areas=_text_tuple(record.research_areas, "research_areas"),
        eligibility=_text_tuple(record.eligibility, "eligibility"),
        application_method=_optional_text(
            record.application_method, "application_method", MAX_TEXT_LENGTH
        ),
        opening_date=opening,
        closing_date=closing,
        start_date=start,
        duration=_optional_text(record.duration, "duration", 200),
        stipend=_optional_text(record.stipend, "stipend", MAX_TEXT_LENGTH),
        tuition=_optional_text(record.tuition, "tuition", MAX_TEXT_LENGTH),
        accommodation=_optional_text(record.accommodation, "accommodation", MAX_TEXT_LENGTH),
        required_documents=_text_tuple(record.required_documents, "required_documents"),
        status=status,
        confidence=confidence,
        sources=sources,
        http_status=http_status,
        raw_payload_path=_optional_text(
            record.raw_payload_path, "raw_payload_path", MAX_URL_LENGTH
        ),
    )


def _required_text(value: str, field: str, max_length: int) -> str:
    if not isinstance(value, str):
        raise DiscoveryError(f"{field} must be a string.")
    if _CONTROL_RE.search(value):
        raise DiscoveryError(f"{field} must not contain control characters.")
    cleaned = " ".join(value.split())
    if not cleaned:
        raise DiscoveryError(f"{field} must not be blank.")
    if len(cleaned) > max_length:
        raise DiscoveryError(f"{field} must be at most {max_length} characters.")
    return cleaned


def _optional_text(value: str | None, field: str, max_length: int) -> str | None:
    if value is None:
        return None
    cleaned = _required_text(value, field, max_length)
    return cleaned


def _query_text(value: str | None, field: str, max_length: int) -> str | None:
    """A query field: shape-checked, then case-folded for cache identity.

    Queries are cache keys, not display strings, so ``"Machine Learning"`` and
    ``"machine learning "`` must produce the same index entry.
    """

    if value is None:
        return None
    return cache.normalise_text(_required_text(value, field, max_length))


def _text_tuple(values: Sequence[str], field: str) -> tuple[str, ...]:
    if isinstance(values, str):
        raise DiscoveryError(f"{field} must be a sequence of strings, not a string.")
    cleaned = tuple(_required_text(item, field, MAX_TEXT_LENGTH) for item in values)
    if len(cleaned) > MAX_ITEMS:
        raise DiscoveryError(f"{field} must contain at most {MAX_ITEMS} items.")
    return tuple(dict.fromkeys(cleaned))


def _optional_date(value: str | None, field: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise DiscoveryError(f"{field} must be an ISO-8601 date string or None.")
    try:
        return date.fromisoformat(value.strip()).isoformat()
    except ValueError as exc:
        raise DiscoveryError(f"{field} must be an ISO-8601 date (YYYY-MM-DD).") from exc


def _confidence(value: float | None, name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DiscoveryError(f"program {name!r} has a non-numeric confidence.")
    number = float(value)
    if not 0.0 <= number <= 1.0:
        raise DiscoveryError(f"program {name!r} has confidence {number}; expected 0.0-1.0.")
    return number


def _url(value: str, field: str) -> str:
    if not isinstance(value, str):
        raise DiscoveryError(f"{field} must be a string.")
    cleaned = value.strip()
    if not cleaned:
        raise DiscoveryError(f"{field} must not be blank.")
    if len(cleaned) > MAX_URL_LENGTH:
        raise DiscoveryError(f"{field} must be at most {MAX_URL_LENGTH} characters.")
    parts = urlsplit(cleaned)
    if parts.scheme not in {"http", "https"}:
        raise DiscoveryError(f"{field} must be an http(s) URL.")
    if not parts.netloc:
        raise DiscoveryError(f"{field} must include a host.")
    if parts.username or parts.password:
        raise DiscoveryError(f"{field} must not embed credentials.")
    return cleaned


# --------------------------------------------------------------------------- #
# search
# --------------------------------------------------------------------------- #


def search_programs(
    connection: sqlite3.Connection,
    query: ProgramQuery,
    *,
    adapter: ProgramAdapter | None = None,
    offline: bool = False,
    now: datetime | None = None,
    actor: str = "discovery",
) -> DiscoveryResult:
    """Answer a program query from the cache, refreshing only when needed.

    Returns cached rows marked ``stale`` when the cache expired, a refresh
    failed, or ``offline`` was requested. An empty answer is cached for
    :data:`EMPTY_RESULT_TTL_DAYS`, so repeating a query that found nothing does
    not reach the source either. Raises :class:`DiscoveryUnavailableError` when
    there is neither a fresh-enough cache entry nor an adapter to fetch one.
    """

    checked_at = now or datetime.now(UTC)
    normalised = query.normalised()
    fingerprint = normalised.fingerprint()
    cached_ids, empty_until = _index_entry(connection, fingerprint)
    if not cached_ids and offline and normalised.is_empty():
        # Offline browsing of the whole cache; a filtered query without an index
        # entry stays empty rather than pretending a filter was applied.
        cached_ids = _all_program_ids(connection)
    if (
        not cached_ids
        and empty_until is not None
        and checked_at < cache.parse_timestamp(empty_until)
    ):
        # A recent "nothing found" answer is a real cache hit: without it a query
        # with no results would call the source on every keystroke-like repeat.
        _audit(
            connection,
            actor=actor,
            action="research.query_served",
            outcome="success",
            metadata={
                "fingerprint": fingerprint,
                "served_from": "cache",
                "programs": 0,
                "stale": False,
                "adapter": None,
            },
        )
        return DiscoveryResult(
            query=normalised,
            programs=(),
            served_from_cache=True,
            stale=False,
            message=None,
        )
    cached = _load_programs(connection, cached_ids, now=checked_at)

    if cached and not _any_stale(cached):
        _audit(
            connection,
            actor=actor,
            action="research.query_served",
            outcome="success",
            metadata={
                "fingerprint": fingerprint,
                "served_from": "cache",
                "programs": len(cached),
                "stale": False,
                "adapter": None,
            },
        )
        return DiscoveryResult(
            query=normalised,
            programs=cached,
            served_from_cache=True,
            stale=False,
            message=None,
        )

    if offline:
        message = (
            None
            if cached
            else (
                "No cached program matches this query yet. Run the query online once "
                "to fill the cache."
            )
        )
        _audit(
            connection,
            actor=actor,
            action="research.query_served",
            outcome="success",
            metadata={
                "fingerprint": fingerprint,
                "served_from": "cache",
                "programs": len(cached),
                "stale": bool(cached),
                "adapter": None,
            },
        )
        return DiscoveryResult(
            query=normalised,
            programs=cached,
            served_from_cache=True,
            stale=bool(cached),
            message=message,
        )

    if adapter is None:
        if cached:
            return DiscoveryResult(
                query=normalised,
                programs=cached,
                served_from_cache=True,
                stale=True,
                message=_stale_message(cached),
            )
        raise DiscoveryUnavailableError(
            "no research discovery adapter is configured, so there is no live source to "
            "query. Configure one, or browse the cache in offline mode."
        )

    try:
        records = tuple(validate_program(record) for record in adapter.search(normalised))
    except Exception as exc:  # adapter boundary: network, parsing or validation
        _record_failure(connection, cached, checked_at, exc)
        _audit(
            connection,
            actor=actor,
            action="research.refresh_failed",
            outcome="failure",
            metadata={
                "fingerprint": fingerprint,
                "adapter": getattr(adapter, "name", adapter.__class__.__name__),
                "error": exc.__class__.__name__,
            },
        )
        if cached:
            return DiscoveryResult(
                query=normalised,
                programs=cached,
                served_from_cache=True,
                stale=True,
                message=_stale_message(cached, failed=True),
            )
        if isinstance(exc, DiscoveryError):
            raise
        raise DiscoveryError(
            f"the discovery source {getattr(adapter, 'name', adapter.__class__.__name__)!r} "
            f"failed ({exc.__class__.__name__}); the cache has no result for this query."
        ) from exc

    with db.transaction(connection):
        ids = tuple(
            _store_program(connection, record, adapter=adapter, now=checked_at)
            for record in records
        )
        _write_index(connection, normalised, fingerprint, ids, now=checked_at)
        _audit(
            connection,
            actor=actor,
            action="research.query_served",
            outcome="success",
            metadata={
                "fingerprint": fingerprint,
                "served_from": "adapter",
                "programs": len(ids),
                "stale": False,
                "adapter": getattr(adapter, "name", adapter.__class__.__name__),
            },
        )

    programs = _load_programs(connection, ids, now=checked_at)
    return DiscoveryResult(
        query=normalised,
        programs=programs,
        served_from_cache=False,
        stale=False,
        message=None,
    )


# --------------------------------------------------------------------------- #
# storage
# --------------------------------------------------------------------------- #


def program_key(record: DiscoveredProgram) -> str:
    """The canonical identity of a program on an official source."""

    return cache.canonical_key(
        "program",
        record.country or "",
        record.institution,
        record.department or "",
        record.name,
    )


def _store_program(
    connection: sqlite3.Connection,
    record: DiscoveredProgram,
    *,
    adapter: ProgramAdapter,
    now: datetime,
) -> int:
    now_iso = cache.to_timestamp(now)
    institution_id = _upsert_institution(connection, record, now_iso=now_iso)
    department_id = _upsert_department(connection, record, institution_id, now_iso=now_iso)
    key = program_key(record)
    row = connection.execute(
        """
        INSERT INTO programs (
            canonical_key, name, institution_id, department_id, country,
            opportunity_type, research_areas_json, official_url, eligibility_json,
            application_method, opening_date, closing_date, start_date, duration_text,
            stipend_text, tuition_text, accommodation_text, required_documents_json,
            status, confidence, last_checked_at, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(canonical_key) DO UPDATE SET
            name = excluded.name,
            institution_id = excluded.institution_id,
            department_id = excluded.department_id,
            country = excluded.country,
            opportunity_type = excluded.opportunity_type,
            research_areas_json = excluded.research_areas_json,
            official_url = excluded.official_url,
            eligibility_json = excluded.eligibility_json,
            application_method = excluded.application_method,
            opening_date = excluded.opening_date,
            closing_date = excluded.closing_date,
            start_date = excluded.start_date,
            duration_text = excluded.duration_text,
            stipend_text = excluded.stipend_text,
            tuition_text = excluded.tuition_text,
            accommodation_text = excluded.accommodation_text,
            required_documents_json = excluded.required_documents_json,
            status = excluded.status,
            confidence = excluded.confidence,
            last_checked_at = excluded.last_checked_at,
            updated_at = excluded.updated_at
        RETURNING id
        """,
        (
            key,
            record.name,
            institution_id,
            department_id,
            record.country,
            record.opportunity_type,
            json.dumps(record.research_areas, ensure_ascii=False),
            record.official_url,
            json.dumps(record.eligibility, ensure_ascii=False),
            record.application_method,
            record.opening_date,
            record.closing_date,
            record.start_date,
            record.duration,
            record.stipend,
            record.tuition,
            record.accommodation,
            json.dumps(record.required_documents, ensure_ascii=False),
            record.status,
            record.confidence,
            now_iso,
            now_iso,
            now_iso,
        ),
    ).fetchone()
    program_id = int(row["id"])

    connection.execute("DELETE FROM program_sources WHERE program_id = ?", (program_id,))
    for source in record.sources:
        connection.execute(
            "INSERT INTO program_sources "
            "(program_id, url, publisher, kind, fetched_at, verified_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (program_id, source.url, source.publisher, source.kind, now_iso, now_iso),
        )

    payload = record_payload(record)
    cache.put_entry(
        connection,
        entity_type="program",
        entity_id=program_id,
        canonical_key=key,
        source_url=record.official_url,
        fetched_at=now_iso,
        verified_at=now_iso,
        ttl_seconds=cache.ttl_seconds(
            "program",
            opening_date=record.opening_date,
            closing_date=record.closing_date,
            now=now,
        ),
        content_hash=cache.content_hash(payload),
        http_status=record.http_status,
        adapter=adapter.name,
        parser_version=adapter.parser_version,
        raw_payload_path=record.raw_payload_path,
    )
    _cache_institution_and_department(
        connection,
        record,
        institution_id=institution_id,
        department_id=department_id,
        adapter=adapter,
        now=now,
        now_iso=now_iso,
    )
    return program_id


def _upsert_institution(
    connection: sqlite3.Connection, record: DiscoveredProgram, *, now_iso: str
) -> int:
    key = cache.canonical_key("institution", record.country or "", record.institution)
    row = connection.execute(
        """
        INSERT INTO institutions (canonical_key, name, country, website_url, created_at, updated_at)
        VALUES (?, ?, ?, NULL, ?, ?)
        ON CONFLICT(canonical_key) DO UPDATE SET
            name = excluded.name,
            country = excluded.country,
            updated_at = excluded.updated_at
        RETURNING id
        """,
        (key, record.institution, record.country, now_iso, now_iso),
    ).fetchone()
    return int(row["id"])


def _upsert_department(
    connection: sqlite3.Connection,
    record: DiscoveredProgram,
    institution_id: int,
    *,
    now_iso: str,
) -> int | None:
    if record.department is None:
        return None
    key = cache.canonical_key(
        "department", record.country or "", record.institution, record.department
    )
    row = connection.execute(
        """
        INSERT INTO departments
            (institution_id, canonical_key, name, website_url, created_at, updated_at)
        VALUES (?, ?, ?, NULL, ?, ?)
        ON CONFLICT(canonical_key) DO UPDATE SET
            institution_id = excluded.institution_id,
            name = excluded.name,
            updated_at = excluded.updated_at
        RETURNING id
        """,
        (institution_id, key, record.department, now_iso, now_iso),
    ).fetchone()
    return int(row["id"])


def _cache_institution_and_department(
    connection: sqlite3.Connection,
    record: DiscoveredProgram,
    *,
    institution_id: int,
    department_id: int | None,
    adapter: ProgramAdapter,
    now: datetime,
    now_iso: str,
) -> None:
    institution_payload = {
        "name": record.institution,
        "country": record.country,
        "source_url": record.official_url,
    }
    cache.put_entry(
        connection,
        entity_type="institution",
        entity_id=institution_id,
        canonical_key=cache.canonical_key("institution", record.country or "", record.institution),
        source_url=record.official_url,
        fetched_at=now_iso,
        verified_at=now_iso,
        ttl_seconds=cache.ttl_seconds("institution"),
        content_hash=cache.content_hash(institution_payload),
        http_status=record.http_status,
        adapter=adapter.name,
        parser_version=adapter.parser_version,
    )
    if department_id is None:
        return
    department_payload = {
        "name": record.department or "",
        "institution": record.institution,
        "source_url": record.official_url,
    }
    cache.put_entry(
        connection,
        entity_type="department",
        entity_id=department_id,
        canonical_key=cache.canonical_key(
            "department", record.country or "", record.institution, record.department or ""
        ),
        source_url=record.official_url,
        fetched_at=now_iso,
        verified_at=now_iso,
        ttl_seconds=cache.ttl_seconds("department"),
        content_hash=cache.content_hash(department_payload),
        http_status=record.http_status,
        adapter=adapter.name,
        parser_version=adapter.parser_version,
    )


def _write_index(
    connection: sqlite3.Connection,
    query: ProgramQuery,
    fingerprint: str,
    ids: Sequence[int],
    *,
    now: datetime,
) -> None:
    now_iso = cache.to_timestamp(now)
    payload = query.as_dict()
    # Only an empty answer needs its own expiry: a non-empty result set is
    # refreshed according to the TTL of its program entries.
    empty_expiry = (
        cache.to_timestamp(now + timedelta(days=EMPTY_RESULT_TTL_DAYS)) if not ids else None
    )
    connection.execute(
        """
        INSERT INTO research_query_index (
            fingerprint, entity_type, research_area, country, department,
            degree_level, entity_ids_json, expires_at, created_at, updated_at
        ) VALUES (?, 'program', ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(fingerprint) DO UPDATE SET
            research_area = excluded.research_area,
            country = excluded.country,
            department = excluded.department,
            degree_level = excluded.degree_level,
            entity_ids_json = excluded.entity_ids_json,
            expires_at = excluded.expires_at,
            updated_at = excluded.updated_at
        """,
        (
            fingerprint,
            payload["research_area"],
            payload["country"],
            payload["department"],
            payload["degree_level"],
            json.dumps(list(ids)),
            empty_expiry,
            now_iso,
            now_iso,
        ),
    )


# --------------------------------------------------------------------------- #
# reads
# --------------------------------------------------------------------------- #


def _index_entry(
    connection: sqlite3.Connection, fingerprint: str
) -> tuple[tuple[int, ...], str | None]:
    """Return the indexed program ids and, for an empty result, its expiry."""

    row = connection.execute(
        "SELECT entity_ids_json, expires_at FROM research_query_index WHERE fingerprint = ?",
        (fingerprint,),
    ).fetchone()
    if row is None:
        return (), None
    try:
        decoded = json.loads(row["entity_ids_json"])
    except (TypeError, ValueError) as exc:
        raise CacheError("the research query index contains invalid JSON.") from exc
    if not isinstance(decoded, list):
        raise CacheError("the research query index must store a list of program ids.")
    expires_at = None if row["expires_at"] is None else str(row["expires_at"])
    return tuple(int(value) for value in decoded), expires_at


def _all_program_ids(connection: sqlite3.Connection) -> tuple[int, ...]:
    rows = connection.execute("SELECT id FROM programs ORDER BY name, id").fetchall()
    return tuple(int(row["id"]) for row in rows)


def _batches(ids: Sequence[int]) -> tuple[tuple[int, ...], ...]:
    """Split ids into unique, ordered batches safe for one SQL statement."""

    unique = tuple(dict.fromkeys(ids))
    return tuple(
        unique[start : start + READ_BATCH_SIZE] for start in range(0, len(unique), READ_BATCH_SIZE)
    )


def _load_programs(
    connection: sqlite3.Connection, ids: Sequence[int], *, now: datetime
) -> tuple[Program, ...]:
    if not ids:
        return ()
    # Offline browsing asks for every program, and SQLite caps the number of
    # bound parameters per statement, so reads are batched.
    batches = _batches(ids)
    rows: list[sqlite3.Row] = []
    sources: dict[int, list[str]] = {}
    entries: dict[int, tuple[cache.CacheEntry, ...]] = {}
    for batch in batches:
        placeholders = ", ".join("?" for _ in batch)
        rows.extend(
            connection.execute(
                f"""
                SELECT p.*, i.name AS institution_name, d.name AS department_name
                FROM programs AS p
                LEFT JOIN institutions AS i ON i.id = p.institution_id
                LEFT JOIN departments AS d ON d.id = p.department_id
                WHERE p.id IN ({placeholders})
                ORDER BY p.name, p.id
                """,  # noqa: S608 - placeholders are generated, ids are bound
                batch,
            ).fetchall()
        )
        for row in connection.execute(
            f"SELECT program_id, url FROM program_sources WHERE program_id IN ({placeholders}) "
            "ORDER BY id",  # noqa: S608
            batch,
        ):
            sources.setdefault(int(row["program_id"]), []).append(str(row["url"]))
        entries.update(
            cache.entries_for_entities(connection, entity_type="program", entity_ids=batch)
        )
    rows.sort(key=lambda row: (str(row["name"]), int(row["id"])))

    programs: list[Program] = []
    for row in rows:
        program_id = int(row["id"])
        entity_entries = entries.get(program_id, ())
        is_stale = not entity_entries or any(entry.is_stale(now) for entry in entity_entries)
        programs.append(
            Program(
                id=program_id,
                canonical_key=str(row["canonical_key"]),
                name=str(row["name"]),
                institution=_row_text(row["institution_name"]),
                department=_row_text(row["department_name"]),
                country=_row_text(row["country"]),
                opportunity_type=_row_text(row["opportunity_type"]),
                research_areas=_json_tuple(row["research_areas_json"]),
                official_url=str(row["official_url"]),
                evidence_urls=tuple(sources.get(program_id, ())),
                eligibility=_json_tuple(row["eligibility_json"]),
                application_method=_row_text(row["application_method"]),
                opening_date=_row_text(row["opening_date"]),
                closing_date=_row_text(row["closing_date"]),
                start_date=_row_text(row["start_date"]),
                duration=_row_text(row["duration_text"]),
                stipend=_row_text(row["stipend_text"]),
                tuition=_row_text(row["tuition_text"]),
                accommodation=_row_text(row["accommodation_text"]),
                required_documents=_json_tuple(row["required_documents_json"]),
                status=str(row["status"]),
                confidence=None if row["confidence"] is None else float(row["confidence"]),
                last_checked_at=_row_text(row["last_checked_at"]),
                stale=is_stale,
            )
        )
    return tuple(programs)


def _row_text(value: Any) -> str | None:
    return None if value is None else str(value)


def _json_tuple(value: Any) -> tuple[str, ...]:
    if not value:
        return ()
    try:
        decoded = json.loads(value)
    except (TypeError, ValueError) as exc:
        raise CacheError("a stored program list contains invalid JSON.") from exc
    if not isinstance(decoded, list):
        raise CacheError("a stored program list must be a JSON array.")
    return tuple(str(item) for item in decoded)


# --------------------------------------------------------------------------- #
# provenance helpers
# --------------------------------------------------------------------------- #


def record_payload(record: DiscoveredProgram) -> dict[str, Any]:
    return {
        "name": record.name,
        "institution": record.institution,
        "official_url": record.official_url,
        "country": record.country,
        "department": record.department,
        "opportunity_type": record.opportunity_type,
        "research_areas": list(record.research_areas),
        "eligibility": list(record.eligibility),
        "application_method": record.application_method,
        "opening_date": record.opening_date,
        "closing_date": record.closing_date,
        "start_date": record.start_date,
        "duration": record.duration,
        "stipend": record.stipend,
        "tuition": record.tuition,
        "accommodation": record.accommodation,
        "required_documents": list(record.required_documents),
        "status": record.status,
        "confidence": record.confidence,
        "sources": [
            {"url": source.url, "publisher": source.publisher, "kind": source.kind}
            for source in record.sources
        ],
    }


def _any_stale(programs: Sequence[Program]) -> bool:
    return any(program.stale for program in programs)


def _stale_message(programs: Sequence[Program], *, failed: bool = False) -> str:
    checked = sorted(
        program.last_checked_at for program in programs if program.last_checked_at is not None
    )
    since = f" last checked {checked[0]}" if checked else ""
    reason = "the last refresh failed" if failed else "the cache entry expired"
    return f"Showing cached results{since}; {reason}. Treat them as stale, not verified."


def _record_failure(
    connection: sqlite3.Connection,
    programs: Sequence[Program],
    now: datetime,
    error: Exception,
) -> None:
    if not programs:
        return
    attempted_at = cache.to_timestamp(now)
    with db.transaction(connection):
        for program in programs:
            cache.mark_refresh_failed(
                connection,
                entity_type="program",
                entity_id=program.id,
                attempted_at=attempted_at,
                error=error.__class__.__name__,
            )


def _audit(
    connection: sqlite3.Connection,
    *,
    actor: str,
    action: str,
    outcome: str,
    metadata: Mapping[str, Any],
) -> None:
    db.record_audit_event(
        connection,
        actor=actor,
        action=action,
        outcome=outcome,
        target_type="research_query",
        target_id=str(metadata.get("fingerprint", "")),
        metadata=metadata,
    )
