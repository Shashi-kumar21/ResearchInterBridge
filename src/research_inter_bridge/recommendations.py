"""Ranked, explained matches for one student over the shared cache (Phase 6c).

The candidates come from the cache-only browse functions, so ranking never
calls a source. Scores are computed live with :mod:`matching` and never stored
here: a snapshot is written only when the student shortlists (ADR-022).

Ranking needs the whole candidate set, so at most :data:`MAX_CANDIDATES`
records matching the filters are scored; the page says when that cap was hit.
Targets the student excluded are left out unless asked for.

The ranked list is kept in a small in-process cache for
:data:`CACHE_TTL_SECONDS`, so "Load more" slices it instead of re-scoring up to
300 records (Owner priority: low latency, cheap to run). The key covers the
database, the student, the filters, the profile fields a score reads, the
student's shortlist statuses, the weights, the scoring version and the date, so
any of those changing is a cache miss. A cached list belongs to one student and
is never served to another. Newly fetched cache records appear within the TTL.

Only ``(target id, MatchResult, shortlist status)`` is cached, never the
records themselves: a full faculty list with papers and abstracts would cost
tens of MB per entry (QA B1 on #41). The records for the requested page are
loaded afterwards as browse summaries (highlighted papers and a count), which
is also all a list row returns (QA n1).
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
import threading
import time
from collections import OrderedDict
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime
from typing import Generic, TypeVar

from . import discovery, matching, profiles
from . import faculty as faculty_service
from . import shortlist as shortlist_service

#: Records scored per request. Each faculty record loads its papers, so this
#: bounds the work a single request can cause.
MAX_CANDIDATES = 300

#: How long a student's ranked list is reused for paging.
CACHE_TTL_SECONDS = 120
#: Ranked lists kept per process; the oldest is dropped first.
CACHE_MAX_ENTRIES = 256

T = TypeVar("T")

_cache: OrderedDict[str, tuple[float, tuple[_RankedEntry, ...], bool]] = OrderedDict()
_cache_lock = threading.Lock()


def clear_cache() -> None:
    """Drop every cached ranked list (tests, or after a bulk cache import)."""

    with _cache_lock:
        _cache.clear()


@dataclass(frozen=True, slots=True)
class Recommendation(Generic[T]):
    record: T
    result: matching.MatchResult
    #: ``"shortlisted"``, ``"excluded"`` or ``None``.
    shortlist_status: str | None


@dataclass(frozen=True, slots=True)
class _RankedEntry:
    """What the cache keeps per candidate: no record, no papers."""

    target_id: int
    result: matching.MatchResult
    shortlist_status: str | None


@dataclass(frozen=True, slots=True)
class RecommendationPage(Generic[T]):
    items: tuple[Recommendation[T], ...]
    total: int
    limit: int
    offset: int
    #: True when more than :data:`MAX_CANDIDATES` records matched the filters.
    truncated: bool


def faculty_matches(
    connection: sqlite3.Connection,
    user_id: int,
    filters: faculty_service.FacultyFilters | None = None,
    *,
    limit: int = discovery.DEFAULT_BROWSE_LIMIT,
    offset: int = 0,
    include_excluded: bool = False,
    today: date | None = None,
    weights: matching.MatchWeights = matching.DEFAULT_WEIGHTS,
) -> RecommendationPage[faculty_service.FacultyMember]:
    """Cached faculty ranked for this student, best first."""

    limit, offset = discovery._page_bounds(limit, offset)

    day = today or datetime.now(UTC).date()
    now = datetime.combine(day, datetime.min.time(), tzinfo=UTC)
    profile = profiles.load_profile(connection, user_id)

    def page(
        page_limit: int, page_offset: int
    ) -> tuple[int, Sequence[faculty_service.FacultyMember]]:
        result = faculty_service.browse_faculty(
            connection, filters, limit=page_limit, offset=page_offset, now=now
        )
        return result.total, result.faculty

    return _ranked(
        connection,
        user_id,
        "faculty",
        filters,
        profile,
        day,
        weights,
        lambda: _collect(page),
        lambda member: matching.score_faculty(profile, member, today=day, weights=weights),
        lambda ids: faculty_service._load_faculty(
            connection, ids, now=now, include_publications=False
        ),
        lambda member: member.id,
        limit=limit,
        offset=offset,
        include_excluded=include_excluded,
    )


def program_matches(
    connection: sqlite3.Connection,
    user_id: int,
    filters: discovery.ProgramFilters | None = None,
    *,
    limit: int = discovery.DEFAULT_BROWSE_LIMIT,
    offset: int = 0,
    include_excluded: bool = False,
    today: date | None = None,
    weights: matching.MatchWeights = matching.DEFAULT_WEIGHTS,
) -> RecommendationPage[discovery.Program]:
    """Cached programs ranked for this student; ineligible ones last."""

    limit, offset = discovery._page_bounds(limit, offset)

    day = today or datetime.now(UTC).date()
    now = datetime.combine(day, datetime.min.time(), tzinfo=UTC)
    profile = profiles.load_profile(connection, user_id)

    def page(page_limit: int, page_offset: int) -> tuple[int, Sequence[discovery.Program]]:
        result = discovery.browse_programs(
            connection, filters, limit=page_limit, offset=page_offset, now=now
        )
        return result.total, result.programs

    return _ranked(
        connection,
        user_id,
        "program",
        filters,
        profile,
        day,
        weights,
        lambda: _collect(page),
        lambda program: matching.score_program(profile, program, today=day, weights=weights),
        lambda ids: discovery._load_programs(connection, ids, now=now),
        lambda program: program.id,
        limit=limit,
        offset=offset,
        include_excluded=include_excluded,
    )


def _collect(
    page: Callable[[int, int], tuple[int, Sequence[T]]],
) -> tuple[list[T], bool]:
    records: list[T] = []
    total = 0
    while len(records) < MAX_CANDIDATES:
        size = min(discovery.MAX_BROWSE_LIMIT, MAX_CANDIDATES - len(records))
        total, batch = page(size, len(records))
        records.extend(batch)
        if not batch or len(records) >= total:
            break
    return records, total > len(records)


def _ranked(
    connection: sqlite3.Connection,
    user_id: int,
    target_type: str,
    filters: object,
    profile: profiles.StudentProfile,
    day: date,
    weights: matching.MatchWeights,
    collect: Callable[[], tuple[list[T], bool]],
    score: Callable[[T], matching.MatchResult],
    load: Callable[[Sequence[int]], Sequence[T]],
    id_of: Callable[[T], int],
    *,
    limit: int,
    offset: int,
    include_excluded: bool,
) -> RecommendationPage[T]:
    statuses = shortlist_service.statuses(connection, user_id, target_type)
    key = _cache_key(
        connection,
        user_id,
        target_type,
        filters,
        profile,
        statuses,
        include_excluded,
        day,
        weights,
    )
    hit = _cache_get(key)
    if hit is not None:
        ranked, truncated = hit
    else:
        records, truncated = collect()
        scored: list[_RankedEntry] = []
        for record in records:
            result = score(record)
            status = statuses.get(result.target_id)
            if status == "excluded" and not include_excluded:
                continue
            scored.append(_RankedEntry(result.target_id, result, status))
        scored.sort(key=lambda item: matching.rank_key(item.result))
        ranked = tuple(scored)
        # The full records (papers, abstracts) are dropped here on purpose.
        _cache_put(key, ranked, truncated)
    window = ranked[offset : offset + limit]
    by_id = {id_of(record): record for record in load([entry.target_id for entry in window])}
    items = tuple(
        Recommendation(
            record=by_id[entry.target_id],
            result=entry.result,
            shortlist_status=entry.shortlist_status,
        )
        for entry in window
        if entry.target_id in by_id
    )
    return RecommendationPage(
        items=items,
        total=len(ranked),
        limit=limit,
        offset=offset,
        truncated=truncated,
    )


def _cache_key(
    connection: sqlite3.Connection,
    user_id: int,
    target_type: str,
    filters: object,
    profile: profiles.StudentProfile,
    statuses: dict[int, str],
    include_excluded: bool,
    day: date,
    weights: matching.MatchWeights,
) -> str:
    database = next(
        (
            str(row["file"])
            for row in connection.execute("PRAGMA database_list")
            if row["name"] == "main"
        ),
        "",
    )
    payload = {
        "db": database,
        "user": user_id,
        "type": target_type,
        "filters": None if filters is None else asdict(filters),  # type: ignore[call-overload]
        "profile": matching._profile_inputs(profile),
        "statuses": sorted(statuses.items()),
        "include_excluded": include_excluded,
        "day": day.isoformat(),
        "weights": weights.as_dict(),
        "version": matching.SCORING_VERSION,
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, default=str).encode("utf-8")
    ).hexdigest()


def _cache_get(key: str) -> tuple[tuple[_RankedEntry, ...], bool] | None:
    with _cache_lock:
        entry = _cache.get(key)
        if entry is None:
            return None
        stored_at, ranked, truncated = entry
        if time.monotonic() - stored_at > CACHE_TTL_SECONDS:
            del _cache[key]
            return None
        _cache.move_to_end(key)
        return ranked, truncated


def _cache_put(key: str, ranked: tuple[_RankedEntry, ...], truncated: bool) -> None:
    with _cache_lock:
        _cache[key] = (time.monotonic(), ranked, truncated)
        _cache.move_to_end(key)
        while len(_cache) > CACHE_MAX_ENTRIES:
            _cache.popitem(last=False)
