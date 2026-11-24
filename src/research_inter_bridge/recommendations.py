"""Ranked, explained matches for one student over the shared cache (Phase 6c).

The candidates come from the cache-only browse functions, so ranking never
calls a source. Scores are computed live with :mod:`matching` and never stored
here: a snapshot is written only when the student shortlists (ADR-022).

Ranking needs the whole candidate set, so at most :data:`MAX_CANDIDATES`
records matching the filters are scored; the page says when that cap was hit.
Targets the student excluded are left out unless asked for.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Generic, TypeVar

from . import discovery, matching, profiles
from . import faculty as faculty_service
from . import shortlist as shortlist_service

#: Records scored per request. Each faculty record loads its papers, so this
#: bounds the work a single request can cause.
MAX_CANDIDATES = 300

T = TypeVar("T")


@dataclass(frozen=True, slots=True)
class Recommendation(Generic[T]):
    record: T
    result: matching.MatchResult
    #: ``"shortlisted"``, ``"excluded"`` or ``None``.
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

    records, truncated = _collect(page)
    return _rank(
        connection,
        user_id,
        records,
        "faculty",
        lambda member: matching.score_faculty(profile, member, today=day, weights=weights),
        limit=limit,
        offset=offset,
        include_excluded=include_excluded,
        truncated=truncated,
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

    records, truncated = _collect(page)
    return _rank(
        connection,
        user_id,
        records,
        "program",
        lambda program: matching.score_program(profile, program, today=day, weights=weights),
        limit=limit,
        offset=offset,
        include_excluded=include_excluded,
        truncated=truncated,
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


def _rank(
    connection: sqlite3.Connection,
    user_id: int,
    records: Sequence[T],
    target_type: str,
    score: Callable[[T], matching.MatchResult],
    *,
    limit: int,
    offset: int,
    include_excluded: bool,
    truncated: bool,
) -> RecommendationPage[T]:
    statuses = shortlist_service.statuses(connection, user_id, target_type)
    scored: list[Recommendation[T]] = []
    for record in records:
        result = score(record)
        status = statuses.get(result.target_id)
        if status == "excluded" and not include_excluded:
            continue
        scored.append(Recommendation(record=record, result=result, shortlist_status=status))
    scored.sort(key=lambda item: matching.rank_key(item.result))
    return RecommendationPage(
        items=tuple(scored[offset : offset + limit]),
        total=len(scored),
        limit=limit,
        offset=offset,
        truncated=truncated,
    )
