from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from research_inter_bridge import db, discovery
from research_inter_bridge import research_cache as cache
from research_inter_bridge.errors import DiscoveryError, DiscoveryUnavailableError

NOW = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
OFFICIAL_URL = "https://www.tum.de/en/studies/programs/msc-research-internship"


@pytest.fixture
def connection(tmp_path: Path):
    with db.session(tmp_path / "app.db") as conn:
        db.apply_migrations(conn)
        yield conn


def make_program(**overrides: object) -> discovery.DiscoveredProgram:
    base: dict[str, object] = {
        "name": "MSc Research Internship",
        "institution": "Technical University of Munich",
        "country": "Germany",
        "department": "Physics",
        "official_url": OFFICIAL_URL,
        "research_areas": ("machine learning", "physics"),
        "closing_date": "2027-01-15",
        "status": "upcoming",
        "confidence": 0.8,
    }
    base.update(overrides)
    return discovery.DiscoveredProgram(**base)  # type: ignore[arg-type]


@dataclass
class FakeAdapter:
    records: tuple[discovery.DiscoveredProgram, ...] = ()
    name: str = "fixture"
    parser_version: str = "1"
    calls: int = 0
    error: Exception | None = None

    def search(self, query: discovery.ProgramQuery) -> tuple[discovery.DiscoveredProgram, ...]:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return self.records


# --------------------------------------------------------------------------- #
# validation
# --------------------------------------------------------------------------- #


def test_validation_normalises_and_adds_the_official_source() -> None:
    cleaned = discovery.validate_program(
        make_program(
            name="  MSc   Research Internship ",
            research_areas=("machine learning", "machine learning", " physics "),
            sources=(
                discovery.SourceRef(
                    url="https://www.tum.de/en/study/application",
                    publisher="TUM",
                    kind="application_page",
                ),
            ),
        )
    )

    assert cleaned.name == "MSc Research Internship"
    assert cleaned.research_areas == ("machine learning", "physics")
    assert cleaned.sources[0].url == OFFICIAL_URL
    assert cleaned.sources[1].kind == "application_page"


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"name": "   "}, "name"),
        ({"official_url": "javascript:alert(1)"}, "http"),
        ({"official_url": "https://user:secret@example.edu"}, "credentials"),
        ({"status": "maybe"}, "status"),
        ({"opening_date": "2027-02-01", "closing_date": "2027-01-01"}, "cannot be after"),
        ({"closing_date": "15/01/2027"}, "ISO-8601"),
        ({"confidence": 1.5}, "confidence"),
        ({"research_areas": ("",)}, "research_areas"),
        ({"research_areas": "machine learning"}, "sequence"),
    ],
)
def test_validation_rejects_bad_records(overrides: dict[str, object], match: str) -> None:
    with pytest.raises(DiscoveryError, match=match):
        discovery.validate_program(make_program(**overrides))


def test_unknown_values_stay_unknown() -> None:
    cleaned = discovery.validate_program(
        make_program(
            status="unverified",
            closing_date=None,
            opening_date=None,
            stipend=None,
            tuition=None,
            eligibility=(),
            confidence=None,
        )
    )

    assert cleaned.status == "unverified"
    assert cleaned.closing_date is None
    assert cleaned.stipend is None
    assert cleaned.eligibility == ()
    assert cleaned.confidence is None


# --------------------------------------------------------------------------- #
# search and cache
# --------------------------------------------------------------------------- #


def test_first_search_stores_the_program_with_provenance(connection: sqlite3.Connection) -> None:
    record = make_program()
    adapter = FakeAdapter(records=(record,))

    result = discovery.search_programs(
        connection, discovery.ProgramQuery(country="Germany"), adapter=adapter, now=NOW
    )

    assert adapter.calls == 1
    assert not result.served_from_cache
    assert not result.stale
    assert len(result.programs) == 1
    program = result.programs[0]
    assert program.name == record.name
    assert program.institution == record.institution
    assert program.department == "Physics"
    assert program.status == "upcoming"
    assert program.stale is False
    assert program.last_checked_at == cache.to_timestamp(NOW)
    assert program.evidence_urls == (OFFICIAL_URL,)

    entry = cache.get_entry(connection, discovery.program_key(record))
    assert entry is not None
    assert entry.adapter == "fixture"
    assert entry.parser_version == "1"
    assert entry.source_url == OFFICIAL_URL
    assert entry.ttl_seconds == 14 * 24 * 60 * 60
    validated = discovery.validate_program(record)
    assert entry.content_hash == cache.content_hash(discovery.record_payload(validated))

    institution = cache.get_entry(
        connection, cache.canonical_key("institution", "Germany", record.institution)
    )
    department = cache.get_entry(
        connection,
        cache.canonical_key("department", "Germany", record.institution, "Physics"),
    )
    assert institution is not None and institution.entity_type == "institution"
    assert department is not None and department.entity_type == "department"

    event = connection.execute(
        "SELECT metadata_json FROM audit_events WHERE action = 'research.query_served'"
    ).fetchone()
    metadata = json.loads(event["metadata_json"])
    assert metadata["served_from"] == "adapter"
    assert metadata["adapter"] == "fixture"
    assert metadata["programs"] == 1


def test_repeat_search_never_calls_the_adapter(connection: sqlite3.Connection) -> None:
    first = discovery.search_programs(
        connection,
        discovery.ProgramQuery(research_area="Machine Learning", country="Germany"),
        adapter=FakeAdapter(records=(make_program(),)),
        now=NOW,
    )
    bomb = FakeAdapter(error=AssertionError("the adapter must not be called on a fresh cache"))

    second = discovery.search_programs(
        connection,
        discovery.ProgramQuery(research_area=" machine learning ", country="GERMANY"),
        adapter=bomb,
        now=NOW + timedelta(days=1),
    )

    assert bomb.calls == 0
    assert second.served_from_cache
    assert not second.stale
    assert [program.id for program in second.programs] == [p.id for p in first.programs]

    entries = connection.execute(
        "SELECT metadata_json FROM audit_events WHERE action = 'research.query_served'"
    ).fetchall()
    assert json.loads(entries[-1]["metadata_json"])["served_from"] == "cache"


def test_an_expired_entry_is_refreshed_in_place(connection: sqlite3.Connection) -> None:
    first = discovery.search_programs(
        connection,
        discovery.ProgramQuery(country="Germany"),
        adapter=FakeAdapter(records=(make_program(),)),
        now=NOW,
    )
    refreshed = make_program(
        status="closed",
        closing_date="2026-10-01",
        stipend="EUR 1,200 per month",
    )
    adapter = FakeAdapter(records=(refreshed,))

    second = discovery.search_programs(
        connection,
        discovery.ProgramQuery(country="Germany"),
        adapter=adapter,
        now=NOW + timedelta(days=15),
    )

    assert adapter.calls == 1
    assert not second.served_from_cache
    assert len(second.programs) == 1
    assert second.programs[0].id == first.programs[0].id
    assert second.programs[0].status == "closed"
    assert second.programs[0].stipend == "EUR 1,200 per month"
    assert second.programs[0].last_checked_at == cache.to_timestamp(NOW + timedelta(days=15))
    count = connection.execute("SELECT COUNT(*) AS c FROM programs").fetchone()["c"]
    assert count == 1


def test_a_failed_refresh_keeps_the_previous_data_and_marks_it_stale(
    connection: sqlite3.Connection,
) -> None:
    discovery.search_programs(
        connection,
        discovery.ProgramQuery(country="Germany"),
        adapter=FakeAdapter(records=(make_program(),)),
        now=NOW,
    )
    adapter = FakeAdapter(error=TimeoutError("provider timed out"))

    result = discovery.search_programs(
        connection,
        discovery.ProgramQuery(country="Germany"),
        adapter=adapter,
        now=NOW + timedelta(days=15),
    )

    assert adapter.calls == 1
    assert result.served_from_cache
    assert result.stale
    assert result.message is not None and "last refresh failed" in result.message
    assert len(result.programs) == 1
    assert result.programs[0].name == "MSc Research Internship"
    assert result.programs[0].status == "upcoming"

    entry = cache.get_entry(connection, discovery.program_key(make_program()))
    assert entry is not None
    assert entry.last_error == "TimeoutError"

    failed = connection.execute(
        "SELECT outcome, metadata_json FROM audit_events WHERE action = 'research.refresh_failed'"
    ).fetchone()
    assert failed["outcome"] == "failure"
    assert json.loads(failed["metadata_json"])["error"] == "TimeoutError"


def test_a_failed_refresh_without_cached_rows_raises(connection: sqlite3.Connection) -> None:
    adapter = FakeAdapter(error=TimeoutError("provider timed out"))

    with pytest.raises(DiscoveryError, match="failed"):
        discovery.search_programs(
            connection, discovery.ProgramQuery(country="Germany"), adapter=adapter, now=NOW
        )


def test_a_bad_record_is_reported_not_stored(connection: sqlite3.Connection) -> None:
    adapter = FakeAdapter(records=(make_program(status="open", official_url="ftp://x"),))

    with pytest.raises(DiscoveryError, match="http"):
        discovery.search_programs(
            connection, discovery.ProgramQuery(country="Germany"), adapter=adapter, now=NOW
        )

    assert connection.execute("SELECT COUNT(*) AS c FROM programs").fetchone()["c"] == 0


def test_offline_mode_never_calls_the_adapter(connection: sqlite3.Connection) -> None:
    discovery.search_programs(
        connection,
        discovery.ProgramQuery(country="Germany"),
        adapter=FakeAdapter(records=(make_program(),)),
        now=NOW,
    )
    bomb = FakeAdapter(error=AssertionError("offline mode must not use an adapter"))

    fresh = discovery.search_programs(
        connection, discovery.ProgramQuery(country="Germany"), adapter=bomb, offline=True, now=NOW
    )
    stale = discovery.search_programs(
        connection,
        discovery.ProgramQuery(country="Germany"),
        adapter=bomb,
        offline=True,
        now=NOW + timedelta(days=15),
    )

    assert bomb.calls == 0
    assert fresh.served_from_cache and not fresh.stale
    assert stale.served_from_cache and stale.stale
    assert len(stale.programs) == 1


def test_offline_browsing_lists_the_whole_cache(connection: sqlite3.Connection) -> None:
    discovery.search_programs(
        connection,
        discovery.ProgramQuery(country="Germany"),
        adapter=FakeAdapter(records=(make_program(),)),
        now=NOW,
    )

    result = discovery.search_programs(connection, discovery.ProgramQuery(), offline=True, now=NOW)

    assert [program.name for program in result.programs] == ["MSc Research Internship"]


def test_browsing_a_large_cache_is_batched_and_sorted(connection: sqlite3.Connection) -> None:
    """A whole-cache browse must not exceed SQLite's bound-parameter limit."""

    records = tuple(
        make_program(
            name=f"Program {index:04d}",
            official_url=f"https://example.edu/program/{index}",
        )
        for index in range(discovery.READ_BATCH_SIZE + 1)
    )

    discovery.search_programs(
        connection,
        discovery.ProgramQuery(country="Germany"),
        adapter=FakeAdapter(records=records),
        now=NOW,
    )
    result = discovery.search_programs(connection, discovery.ProgramQuery(), offline=True, now=NOW)

    assert len(result.programs) == len(records)
    names = [program.name for program in result.programs]
    assert names == sorted(names)


def test_offline_with_an_empty_cache_is_an_empty_answer_not_an_error(
    connection: sqlite3.Connection,
) -> None:
    result = discovery.search_programs(
        connection, discovery.ProgramQuery(country="Germany"), offline=True, now=NOW
    )

    assert result.programs == ()
    assert result.served_from_cache
    assert result.message is not None and "No cached program" in result.message


def test_an_empty_result_is_cached_briefly(connection: sqlite3.Connection) -> None:
    empty = FakeAdapter(records=())
    first = discovery.search_programs(
        connection, discovery.ProgramQuery(country="Germany"), adapter=empty, now=NOW
    )
    assert empty.calls == 1
    assert first.programs == () and not first.served_from_cache

    bomb = FakeAdapter(error=AssertionError("a cached empty answer must not call the adapter"))
    second = discovery.search_programs(
        connection,
        discovery.ProgramQuery(country="GERMANY"),
        adapter=bomb,
        now=NOW + timedelta(hours=1),
    )
    assert bomb.calls == 0
    assert second.served_from_cache and second.programs == () and not second.stale

    third = discovery.search_programs(
        connection,
        discovery.ProgramQuery(country="Germany"),
        adapter=FakeAdapter(records=(make_program(),)),
        now=NOW + timedelta(days=2),
    )
    assert not third.served_from_cache
    assert len(third.programs) == 1
    assert third.programs[0].stale is False


def test_no_adapter_and_no_cache_is_actionable(connection: sqlite3.Connection) -> None:
    with pytest.raises(DiscoveryUnavailableError, match="no research discovery adapter"):
        discovery.search_programs(connection, discovery.ProgramQuery(country="Germany"), now=NOW)


def test_programs_are_public_and_reused_across_connections(
    connection: sqlite3.Connection, tmp_path: Path
) -> None:
    discovery.search_programs(
        connection,
        discovery.ProgramQuery(country="Germany"),
        adapter=FakeAdapter(records=(make_program(),)),
        now=NOW,
    )

    with db.session(tmp_path / "app.db") as other:
        result = discovery.search_programs(
            other, discovery.ProgramQuery(country="Germany"), offline=True, now=NOW
        )

    assert len(result.programs) == 1
    columns = {
        str(row["name"]) for row in connection.execute("PRAGMA table_info(programs)").fetchall()
    }
    assert "user_id" not in columns


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def test_cli_offline_lists_cached_programs(cli) -> None:
    with db.session(cli.settings.database_path) as connection:
        db.apply_migrations(connection)
        discovery.search_programs(
            connection,
            discovery.ProgramQuery(country="Germany"),
            adapter=FakeAdapter(records=(make_program(),)),
            now=datetime.now(UTC),
        )

    code, out = cli.run("discover", "--offline", "--country", "Germany")

    assert code == 0
    payload = json.loads(out)
    assert payload["served_from_cache"] is True
    assert payload["stale"] is False
    assert payload["programs"][0]["name"] == "MSc Research Internship"
    assert payload["programs"][0]["official_url"] == OFFICIAL_URL
    assert payload["programs"][0]["status"] == "upcoming"


def test_cli_offline_on_an_empty_cache_prints_a_message(cli) -> None:
    code, out = cli.run("discover", "--offline", "--country", "Germany")

    assert code == 0
    payload = json.loads(out)
    assert payload["programs"] == []
    assert "No cached program" in payload["message"]


def test_cli_without_adapter_or_cache_explains_the_blocker(cli) -> None:
    code, out = cli.run("discover", "--country", "Germany")

    assert code == 1
    assert out == ""
    assert "no research discovery adapter" in cli.last_err
