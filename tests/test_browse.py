"""Cache-only browse reads for the research screens (``task.md`` §13.2).

These tests seed the shared cache through the normal discovery path, then read
it back through the browse functions. No test opens a socket: browsing must work
with the adapter disabled, and a fresh cache must not need one.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from research_inter_bridge import db, discovery
from research_inter_bridge import research_cache as cache
from research_inter_bridge.errors import (
    DiscoveryError,
    InstitutionNotFoundError,
    ProgramNotFoundError,
)

NOW = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)


@pytest.fixture
def connection(tmp_path: Path):
    with db.session(tmp_path / "app.db") as conn:
        db.apply_migrations(conn)
        yield conn


@dataclass
class FakeAdapter:
    records: tuple[discovery.DiscoveredProgram, ...] = ()
    name: str = "fixture"
    parser_version: str = "1"
    calls: int = 0

    def search(self, query: discovery.ProgramQuery) -> tuple[discovery.DiscoveredProgram, ...]:
        self.calls += 1
        return self.records


def make_program(**overrides: object) -> discovery.DiscoveredProgram:
    base: dict[str, object] = {
        "name": "MSc Research Internship",
        "institution": "Technical University of Munich",
        "country": "Germany",
        "department": "Physics",
        "degree_level": "MSc",
        "official_url": "https://www.tum.de/en/studies/programs/msc-research-internship",
        "research_areas": ("machine learning", "physics"),
        "closing_date": "2027-01-15",
        "status": "upcoming",
        "confidence": 0.8,
    }
    base.update(overrides)
    return discovery.DiscoveredProgram(**base)  # type: ignore[arg-type]


TUM_MSC = make_program()
TUM_BSC = make_program(
    name="BSc Physics",
    degree_level="BSc",
    research_areas=("physics",),
    official_url="https://www.tum.de/en/studies/programs/bsc-physics",
)
DELFT_MSC = make_program(
    name="MSc Data Science",
    institution="Delft University of Technology",
    country="Netherlands",
    department="Computer Science",
    degree_level="MSc",
    research_areas=("data science",),
    official_url="https://www.tudelft.nl/en/education/programmes/masters/data-science",
)
ETH_PHD = make_program(
    name="PhD Robotics",
    institution="ETH Zurich",
    country="Switzerland",
    department="Robotics",
    degree_level="PhD",
    research_areas=("robotics",),
    official_url="https://ethz.ch/en/doctorate/robotics",
)
ALL_RECORDS = (TUM_MSC, TUM_BSC, DELFT_MSC, ETH_PHD)


def seed(
    connection: sqlite3.Connection,
    records: tuple[discovery.DiscoveredProgram, ...] = ALL_RECORDS,
    *,
    now: datetime = NOW,
) -> FakeAdapter:
    adapter = FakeAdapter(records=records)
    discovery.search_programs(
        connection,
        discovery.ProgramQuery(country="Germany"),
        adapter=adapter,
        now=now,
    )
    return adapter


def names(page: discovery.ProgramPage) -> list[str]:
    return [program.name for program in page.programs]


def institution_id(connection: sqlite3.Connection, name: str) -> int:
    page = discovery.browse_institutions(connection, limit=discovery.MAX_BROWSE_LIMIT, now=NOW)
    return next(item.id for item in page.institutions if item.name == name)


def department_id(connection: sqlite3.Connection, institution: str, name: str) -> int:
    page = discovery.browse_departments(
        connection, institution_id(connection, institution), now=NOW
    )
    return next(item.id for item in page.departments if item.name == name)


# --------------------------------------------------------------------------- #
# programs
# --------------------------------------------------------------------------- #


def test_browse_programs_pages_in_name_order(connection: sqlite3.Connection) -> None:
    seed(connection)

    first = discovery.browse_programs(connection, limit=2, offset=0, now=NOW)
    second = discovery.browse_programs(connection, limit=2, offset=2, now=NOW)

    assert names(first) == ["BSc Physics", "MSc Data Science"]
    assert first.total == 4
    assert (first.limit, first.offset) == (2, 0)
    assert names(second) == ["MSc Research Internship", "PhD Robotics"]
    assert second.total == 4
    assert second.offset == 2


def test_an_offset_past_the_end_is_an_empty_page_not_an_error(
    connection: sqlite3.Connection,
) -> None:
    seed(connection)

    page = discovery.browse_programs(connection, limit=10, offset=50, now=NOW)

    assert page.programs == ()
    assert page.total == 4


def test_browse_programs_filters_by_every_field(connection: sqlite3.Connection) -> None:
    seed(connection)
    tum = institution_id(connection, "Technical University of Munich")
    physics = department_id(connection, "Technical University of Munich", "Physics")

    by_country = discovery.browse_programs(
        connection, discovery.ProgramFilters(country="germany"), now=NOW
    )
    by_master = discovery.browse_programs(
        connection, discovery.ProgramFilters(degree_level="MSc"), now=NOW
    )
    by_alias = discovery.browse_programs(
        connection, discovery.ProgramFilters(degree_level="masters"), now=NOW
    )
    by_phd = discovery.browse_programs(
        connection, discovery.ProgramFilters(degree_level="PhD"), now=NOW
    )
    by_department = discovery.browse_programs(
        connection, discovery.ProgramFilters(department="physics"), now=NOW
    )
    by_institution_name = discovery.browse_programs(
        connection, discovery.ProgramFilters(department="delft"), now=NOW
    )
    by_area = discovery.browse_programs(
        connection, discovery.ProgramFilters(research_area="machine learning"), now=NOW
    )
    by_institution_id = discovery.browse_programs(
        connection, discovery.ProgramFilters(institution_id=tum), now=NOW
    )
    by_department_id = discovery.browse_programs(
        connection, discovery.ProgramFilters(department_id=physics), now=NOW
    )

    assert names(by_country) == ["BSc Physics", "MSc Research Internship"]
    assert names(by_master) == ["MSc Data Science", "MSc Research Internship"]
    assert names(by_alias) == ["MSc Data Science", "MSc Research Internship"]
    assert names(by_phd) == ["PhD Robotics"]
    assert names(by_department) == ["BSc Physics", "MSc Research Internship"]
    # The department filter also matches the institution name.
    assert names(by_institution_name) == ["MSc Data Science"]
    assert names(by_area) == ["MSc Research Internship"]
    assert names(by_institution_id) == ["BSc Physics", "MSc Research Internship"]
    assert names(by_department_id) == ["BSc Physics", "MSc Research Internship"]


def test_filters_combine_with_and_semantics(connection: sqlite3.Connection) -> None:
    seed(connection)

    page = discovery.browse_programs(
        connection,
        discovery.ProgramFilters(country="germany", degree_level="bachelor"),
        now=NOW,
    )

    assert names(page) == ["BSc Physics"]


def test_unknown_values_never_match_a_stated_filter(connection: sqlite3.Connection) -> None:
    seed(
        connection,
        (
            make_program(
                name="Unknown level",
                country=None,
                degree_level=None,
                department=None,
                research_areas=(),
                official_url="https://example.edu/unknown",
            ),
        ),
    )

    assert (
        discovery.browse_programs(
            connection, discovery.ProgramFilters(country="Germany"), now=NOW
        ).programs
        == ()
    )
    assert (
        discovery.browse_programs(
            connection, discovery.ProgramFilters(degree_level="master"), now=NOW
        ).programs
        == ()
    )
    assert (
        discovery.browse_programs(
            connection, discovery.ProgramFilters(department="physics"), now=NOW
        ).programs
        == ()
    )
    assert (
        discovery.browse_programs(
            connection, discovery.ProgramFilters(research_area="physics"), now=NOW
        ).programs
        == ()
    )
    assert len(discovery.browse_programs(connection, now=NOW).programs) == 1


@pytest.mark.parametrize(
    ("limit", "offset"),
    [(0, 0), (discovery.MAX_BROWSE_LIMIT + 1, 0), (10, -1), (True, 0)],
)
def test_invalid_page_bounds_are_rejected(
    connection: sqlite3.Connection, limit: int, offset: int
) -> None:
    with pytest.raises(DiscoveryError):
        discovery.browse_programs(connection, limit=limit, offset=offset, now=NOW)


def test_get_program_returns_detail_and_a_missing_id_is_actionable(
    connection: sqlite3.Connection,
) -> None:
    seed(connection)
    stored = discovery.browse_programs(connection, now=NOW).programs[0]

    program = discovery.get_program(connection, stored.id, now=NOW)

    assert program.id == stored.id
    assert program.institution_id is not None
    assert program.department_id is not None
    assert program.official_url in program.evidence_urls
    assert program.canonical_key.startswith("program:")

    with pytest.raises(ProgramNotFoundError, match="9999") as missing:
        discovery.get_program(connection, 9999, now=NOW)
    assert missing.value.code == "program_missing"


# --------------------------------------------------------------------------- #
# institutions and departments
# --------------------------------------------------------------------------- #


def test_browse_institutions_reports_counts_and_sorts_by_name(
    connection: sqlite3.Connection,
) -> None:
    seed(connection)

    page = discovery.browse_institutions(connection, now=NOW)

    assert [item.name for item in page.institutions] == [
        "Delft University of Technology",
        "ETH Zurich",
        "Technical University of Munich",
    ]
    assert page.total == 3
    tum = next(item for item in page.institutions if "Munich" in item.name)
    assert (tum.program_count, tum.department_count) == (2, 1)
    assert tum.country == "Germany"
    assert tum.last_checked_at is not None
    assert tum.stale is False

    german = discovery.browse_institutions(connection, country="germany", now=NOW)
    assert [item.name for item in german.institutions] == ["Technical University of Munich"]


def test_get_institution_rejects_an_unknown_id(connection: sqlite3.Connection) -> None:
    with pytest.raises(InstitutionNotFoundError, match="4242") as missing:
        discovery.get_institution(connection, 4242, now=NOW)
    assert missing.value.code == "institution_missing"


def test_browse_departments_is_scoped_to_its_institution(
    connection: sqlite3.Connection,
) -> None:
    seed(connection)
    tum = institution_id(connection, "Technical University of Munich")

    page = discovery.browse_departments(connection, tum, now=NOW)

    assert [department.name for department in page.departments] == ["Physics"]
    assert page.departments[0].program_count == 2
    assert page.departments[0].institution_id == tum
    assert page.departments[0].last_checked_at is not None
    assert page.departments[0].stale is False


def test_departments_of_unknown_institution_is_not_an_empty_page(
    connection: sqlite3.Connection,
) -> None:
    with pytest.raises(InstitutionNotFoundError):
        discovery.browse_departments(connection, 4242, now=NOW)


def test_an_institution_without_departments_is_an_empty_page(
    connection: sqlite3.Connection,
) -> None:
    seed(
        connection,
        (
            make_program(
                name="No department",
                department=None,
                official_url="https://example.edu/no-department",
            ),
        ),
    )
    institution = discovery.browse_institutions(connection, now=NOW).institutions[0]

    page = discovery.browse_departments(connection, institution.id, now=NOW)

    assert page.departments == ()
    assert page.total == 0


# --------------------------------------------------------------------------- #
# freshness and provenance
# --------------------------------------------------------------------------- #


def test_browsing_marks_every_level_stale_after_its_ttl(connection: sqlite3.Connection) -> None:
    seed(connection)
    later = NOW + timedelta(days=91)

    programs = discovery.browse_programs(connection, now=later)
    institutions = discovery.browse_institutions(connection, now=later)
    departments = discovery.browse_departments(
        connection, institutions.institutions[0].id, now=later
    )

    assert programs.programs
    assert all(program.stale for program in programs.programs)
    assert all(institution.stale for institution in institutions.institutions)
    assert all(department.stale for department in departments.departments)


def test_browsing_works_without_configuring_any_adapter(connection: sqlite3.Connection) -> None:
    seed(connection)

    # No adapter is ever passed here: browsing is a pure cache read (§13.2.5).
    page = discovery.browse_programs(connection, now=NOW)

    assert len(page.programs) == len(ALL_RECORDS)
    assert page.programs[0].stale is False


def test_degree_levels_are_stored_canonically(connection: sqlite3.Connection) -> None:
    assert discovery.normalise_degree_level("MSc") == "master"
    assert discovery.normalise_degree_level("  Master of Science ") == "master"
    assert discovery.normalise_degree_level("BSc") == "bachelor"
    assert discovery.normalise_degree_level("PhD") == "phd"
    assert discovery.normalise_degree_level("Diploma") == "Diploma"
    assert discovery.normalise_degree_level("   ") is None

    cleaned = discovery.validate_program(make_program(degree_level="MSc"))
    assert cleaned.degree_level == "master"


def test_browse_rows_carry_the_cache_timestamp(connection: sqlite3.Connection) -> None:
    seed(connection)

    page = discovery.browse_programs(connection, now=NOW)
    program = next(item for item in page.programs if item.name == "MSc Research Internship")
    entry = cache.get_entry(connection, program.canonical_key)

    assert entry is not None
    assert program.last_checked_at == entry.verified_at == cache.to_timestamp(NOW)
