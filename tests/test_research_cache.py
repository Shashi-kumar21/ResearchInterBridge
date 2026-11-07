from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from research_inter_bridge import db
from research_inter_bridge import research_cache as cache
from research_inter_bridge.errors import CacheError

NOW = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)


@pytest.fixture
def connection(tmp_path: Path):
    with db.session(tmp_path / "app.db") as conn:
        db.apply_migrations(conn)
        yield conn


def test_canonical_key_is_readable_and_stable() -> None:
    key = cache.canonical_key("program", "Germany", "Technical University of Munich", "MSc")

    assert key == "program:germany:technical-university-of-munich:msc"
    assert key == cache.canonical_key(
        "program", " germany ", "technical  university of munich", "MSC"
    )
    assert (
        cache.canonical_key("institution", "Universität Zürich") == "institution:universität-zürich"
    )


def test_canonical_key_rejects_an_empty_input() -> None:
    with pytest.raises(CacheError, match="non-empty"):
        cache.canonical_key("  ", "")


def test_content_hash_is_order_independent_but_content_sensitive() -> None:
    first = cache.content_hash({"a": 1, "b": [2, 3]})
    second = cache.content_hash({"b": [2, 3], "a": 1})

    assert first == second
    assert first != cache.content_hash({"a": 1, "b": [2, 4]})


def test_ttl_follows_the_task_table() -> None:
    assert cache.ttl_seconds("institution") == 90 * 24 * 60 * 60
    assert cache.ttl_seconds("department") == 90 * 24 * 60 * 60
    assert cache.ttl_seconds("faculty") == 30 * 24 * 60 * 60
    assert cache.ttl_seconds("publication") == 30 * 24 * 60 * 60
    # Outside an application window, or with no dates at all.
    assert cache.ttl_seconds("program", now=NOW) == 14 * 24 * 60 * 60
    assert cache.ttl_seconds("program", closing_date="2027-03-01", now=NOW) == 14 * 24 * 60 * 60
    # Inside an open window.
    assert (
        cache.ttl_seconds("program", opening_date="2026-11-01", closing_date="2027-03-01", now=NOW)
        == 3 * 24 * 60 * 60
    )


def test_ttl_drops_to_one_day_near_or_after_a_deadline() -> None:
    assert cache.ttl_seconds("program", closing_date="2026-11-10", now=NOW) == 24 * 60 * 60
    assert cache.ttl_seconds("program", closing_date="2026-10-01", now=NOW) == 24 * 60 * 60


def test_unknown_entity_type_or_bad_date_is_rejected() -> None:
    with pytest.raises(CacheError, match="unknown cache entity type"):
        cache.ttl_seconds("professor")
    with pytest.raises(CacheError, match="ISO-8601"):
        cache.ttl_seconds("program", closing_date="15/01/2027", now=NOW)


def _put(
    connection: sqlite3.Connection,
    *,
    canonical_key: str = "program:de:tu-munich:msc",
    verified_at: str | None = "2026-11-05T12:00:00.000000+00:00",
    ttl_seconds: int = 14 * 24 * 60 * 60,
) -> int:
    return cache.put_entry(
        connection,
        entity_type="program",
        entity_id=1,
        canonical_key=canonical_key,
        source_url="https://example.edu/program",
        fetched_at="2026-11-05T12:00:00.000000+00:00",
        verified_at=verified_at,
        ttl_seconds=ttl_seconds,
        content_hash="a" * 64,
        http_status=200,
        adapter="fixture",
        parser_version="1",
    )


def test_entry_is_fresh_until_its_ttl(connection: sqlite3.Connection) -> None:
    _put(connection)
    entry = cache.get_entry(connection, "program:de:tu-munich:msc")

    assert entry is not None
    assert entry.checked_at == NOW
    assert not entry.is_stale(NOW + timedelta(days=13))
    assert entry.is_stale(NOW + timedelta(days=15))


def test_failed_refresh_marks_the_entry_stale_without_losing_data(
    connection: sqlite3.Connection,
) -> None:
    _put(connection)

    assert (
        cache.mark_refresh_failed(
            connection,
            entity_type="program",
            entity_id=1,
            attempted_at="2026-11-06T12:00:00.000000+00:00",
            error="TimeoutError",
        )
        == 1
    )
    entry = cache.get_entry(connection, "program:de:tu-munich:msc")

    assert entry is not None
    assert entry.is_stale(NOW + timedelta(days=1))
    assert entry.last_error == "TimeoutError"
    assert entry.content_hash == "a" * 64


def test_a_successful_write_clears_the_failure(connection: sqlite3.Connection) -> None:
    _put(connection)
    cache.mark_refresh_failed(
        connection,
        entity_type="program",
        entity_id=1,
        attempted_at="2026-11-06T12:00:00.000000+00:00",
        error="TimeoutError",
    )

    _put(connection)

    entry = cache.get_entry(connection, "program:de:tu-munich:msc")
    assert entry is not None
    assert entry.last_error is None
    assert cache.list_entries(connection) != ()


def test_cache_rejects_invalid_provenance(connection: sqlite3.Connection) -> None:
    with pytest.raises(CacheError, match="positive"):
        _put(connection, ttl_seconds=0)
    with pytest.raises(CacheError, match="unknown cache entity type"):
        cache.put_entry(
            connection,
            entity_type="student",
            entity_id=1,
            canonical_key="student:1",
            source_url="https://example.edu",
            fetched_at="2026-11-05T12:00:00.000000+00:00",
            verified_at=None,
            ttl_seconds=60,
            content_hash="a" * 64,
            http_status=None,
            adapter="fixture",
            parser_version="1",
        )


def test_batch_entry_reads_stay_under_a_low_parameter_limit(
    connection: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    for entity_id in range(1, 26):
        cache.put_entry(
            connection,
            entity_type="program",
            entity_id=entity_id,
            canonical_key=f"program:test:{entity_id}",
            source_url="https://example.edu/program",
            fetched_at="2026-11-05T12:00:00.000000+00:00",
            verified_at="2026-11-05T12:00:00.000000+00:00",
            ttl_seconds=14 * 24 * 60 * 60,
            content_hash="a" * 64,
            http_status=200,
            adapter="fixture",
            parser_version="1",
        )

    previous_limit = connection.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, 15)
    monkeypatch.setattr(cache, "READ_BATCH_SIZE", 10)
    try:
        grouped = cache.entries_for_entities(
            connection, entity_type="program", entity_ids=range(1, 26)
        )
    finally:
        connection.setlimit(sqlite3.SQLITE_LIMIT_VARIABLE_NUMBER, previous_limit)

    assert sorted(grouped) == list(range(1, 26))
    assert all(len(entries) == 1 for entries in grouped.values())
