"""Fixed-window rate limiter: windows, subjects, sweeps and stored format."""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from pathlib import Path

import pytest

from research_inter_bridge import db, ratelimit
from research_inter_bridge.errors import RateLimitError

CLIENT = "203.0.113.9"
OTHER_CLIENT = "198.51.100.7"


@pytest.fixture
def connection(tmp_path: Path) -> Iterator[sqlite3.Connection]:
    with db.session(tmp_path / "app.db") as conn:
        db.apply_migrations(conn)
        yield conn


@pytest.fixture
def rule() -> ratelimit.RateLimitRule:
    return ratelimit.RateLimitRule(name="test.bucket", limit=3, window_seconds=60)


def test_rules_reject_impossible_configuration() -> None:
    with pytest.raises(ValueError, match="name"):
        ratelimit.RateLimitRule(name="", limit=1, window_seconds=60)
    with pytest.raises(ValueError, match="at least 1"):
        ratelimit.RateLimitRule(name="x", limit=0, window_seconds=60)
    with pytest.raises(ValueError, match="at least 1 second"):
        ratelimit.RateLimitRule(name="x", limit=1, window_seconds=0)


def test_requests_up_to_the_limit_pass_and_the_next_is_refused(
    connection: sqlite3.Connection, rule: ratelimit.RateLimitRule
) -> None:
    for _ in range(rule.limit):
        ratelimit.check(connection, rule=rule, client=CLIENT, now=1000)

    with pytest.raises(RateLimitError) as excinfo:
        ratelimit.check(connection, rule=rule, client=CLIENT, now=1000)

    # 1000 is 40 s into the 960..1019 window, so the caller waits 20 s.
    assert excinfo.value.retry_after == 20
    assert CLIENT not in str(excinfo.value)


def test_the_window_resets_once_it_elapses(
    connection: sqlite3.Connection, rule: ratelimit.RateLimitRule
) -> None:
    for _ in range(rule.limit):
        ratelimit.check(connection, rule=rule, client=CLIENT, now=1000)
    with pytest.raises(RateLimitError):
        ratelimit.check(connection, rule=rule, client=CLIENT, now=1000)

    ratelimit.check(connection, rule=rule, client=CLIENT, now=1060)

    row = connection.execute(
        "SELECT window_start, count FROM rate_limit_counters WHERE subject = ?",
        (ratelimit.subject_for(CLIENT),),
    ).fetchall()
    assert [tuple(row) for row in row] == [(1020, 1)]


def test_refused_requests_keep_counting(
    connection: sqlite3.Connection, rule: ratelimit.RateLimitRule
) -> None:
    for _ in range(rule.limit):
        ratelimit.check(connection, rule=rule, client=CLIENT, now=1000)

    for _ in range(5):
        with pytest.raises(RateLimitError):
            ratelimit.check(connection, rule=rule, client=CLIENT, now=1000)

    stored = connection.execute(
        "SELECT count FROM rate_limit_counters WHERE subject = ?",
        (ratelimit.subject_for(CLIENT),),
    ).fetchone()
    assert stored["count"] == rule.limit + 5


def test_each_client_has_its_own_budget(
    connection: sqlite3.Connection, rule: ratelimit.RateLimitRule
) -> None:
    for _ in range(rule.limit):
        ratelimit.check(connection, rule=rule, client=CLIENT, now=1000)

    ratelimit.check(connection, rule=rule, client=OTHER_CLIENT, now=1000)


def test_the_stored_subject_is_a_digest_not_an_address(
    connection: sqlite3.Connection, rule: ratelimit.RateLimitRule
) -> None:
    ratelimit.check(connection, rule=rule, client=CLIENT, now=1000)

    row = connection.execute("SELECT subject FROM rate_limit_counters").fetchone()

    assert row["subject"] == ratelimit.subject_for(CLIENT)
    assert row["subject"] != CLIENT
    assert len(row["subject"]) == 64


def test_the_same_address_in_two_buckets_has_two_rows(
    connection: sqlite3.Connection, rule: ratelimit.RateLimitRule
) -> None:
    other = ratelimit.RateLimitRule(name="test.other", limit=3, window_seconds=60)

    ratelimit.check(connection, rule=rule, client=CLIENT, now=1000)
    ratelimit.check(connection, rule=other, client=CLIENT, now=1000)

    buckets = connection.execute(
        "SELECT bucket FROM rate_limit_counters ORDER BY bucket"
    ).fetchall()
    assert [row["bucket"] for row in buckets] == ["test.bucket", "test.other"]


def test_a_new_window_removes_the_previous_window_for_that_subject(
    connection: sqlite3.Connection, rule: ratelimit.RateLimitRule
) -> None:
    ratelimit.check(connection, rule=rule, client=CLIENT, now=1000)
    ratelimit.check(connection, rule=rule, client=CLIENT, now=1060)

    windows = connection.execute(
        "SELECT window_start FROM rate_limit_counters WHERE subject = ?",
        (ratelimit.subject_for(CLIENT),),
    ).fetchall()
    assert [row["window_start"] for row in windows] == [1020]


def test_expired_rows_from_other_subjects_are_swept(
    connection: sqlite3.Connection, rule: ratelimit.RateLimitRule
) -> None:
    ratelimit.check(connection, rule=rule, client=OTHER_CLIENT, now=1000)

    # Far beyond the four-window retention of the first row.
    ratelimit.check(connection, rule=rule, client=CLIENT, now=100_000)

    subjects = connection.execute("SELECT subject FROM rate_limit_counters").fetchall()
    assert [row["subject"] for row in subjects] == [ratelimit.subject_for(CLIENT)]


def test_a_short_window_sweep_keeps_a_live_long_window_counter(
    connection: sqlite3.Connection,
) -> None:
    long_rule = ratelimit.RateLimitRule(name="test.long", limit=3, window_seconds=3600)
    short_rule = ratelimit.RateLimitRule(name="test.short", limit=3, window_seconds=60)
    ratelimit.check(connection, rule=long_rule, client=OTHER_CLIENT, now=1000)

    # Counts for a different subject, so this triggers the sweep.
    ratelimit.check(connection, rule=short_rule, client=CLIENT, now=1000)

    buckets = connection.execute(
        "SELECT bucket FROM rate_limit_counters ORDER BY bucket"
    ).fetchall()
    assert [row["bucket"] for row in buckets] == ["test.long", "test.short"]


def test_an_empty_client_is_counted_under_one_shared_subject(
    connection: sqlite3.Connection, rule: ratelimit.RateLimitRule
) -> None:
    ratelimit.check(connection, rule=rule, client="", now=1000)

    row = connection.execute("SELECT subject FROM rate_limit_counters").fetchone()
    assert row["subject"] == ratelimit.subject_for("unknown")


def test_the_shipped_rules_are_bounded_and_documented() -> None:
    assert (ratelimit.REGISTER_RULE.limit, ratelimit.REGISTER_RULE.window_seconds) == (30, 3600)
    assert (ratelimit.LOGIN_RULE.limit, ratelimit.LOGIN_RULE.window_seconds) == (120, 900)
