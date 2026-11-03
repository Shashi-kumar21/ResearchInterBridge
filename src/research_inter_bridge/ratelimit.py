"""Fixed-window rate limiting for the unauthenticated API endpoints.

Phase 2d-3 of ``task.md`` (§3.1, §8). Rules encoded here:

* The account-lockout counter in ``accounts`` protects *one* account. It does
  nothing for registration, or for a login flood spread across many addresses,
  and every attempt costs one scrypt hash. This module caps the work a single
  caller can request per window *before* any password hashing happens.
* Counters live in SQLite rather than memory so a restart does not reset a
  caller's budget and a second process shares the same state.
* The subject is the transport peer address, never ``X-Forwarded-For``: that
  header is caller-controlled, so trusting it would let one client mint a new
  budget per request. An operator behind a reverse proxy terminates TLS there
  and should not enable header rewriting unless the proxy is trusted to set it.
* The address is hashed with SHA-256 before storage, so the counter table is not
  a record of who tried to sign in.
* A window is fixed, not sliding: cheap, no per-request row growth, and the
  worst case a caller can gain is one extra window at a boundary.
"""

from __future__ import annotations

import hashlib
import sqlite3
import time
from dataclasses import dataclass

from . import db
from .errors import RateLimitError

#: How many old windows stay in the table before a sweep removes them. Four
#: windows of slack absorbs clock drift between processes without letting the
#: table grow without bound.
STALE_WINDOW_MULTIPLIER = 4


@dataclass(frozen=True, slots=True)
class RateLimitRule:
    """A named budget: at most ``limit`` requests per ``window_seconds``.

    ``name`` is the bucket stored in the database, so changing it deliberately
    starts a fresh budget. ``limit`` counts every request, including rejected
    ones, otherwise a caller could probe past the limit for free.
    """

    name: str
    limit: int
    window_seconds: int

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("rate limit rule name must not be empty.")
        if self.limit < 1:
            raise ValueError(f"rate limit for {self.name!r} must be at least 1.")
        if self.window_seconds < 1:
            raise ValueError(f"rate limit window for {self.name!r} must be at least 1 second.")


#: Registration is cheap once per student and expensive per call (one scrypt
#: hash), so the budget is generous for a shared campus NAT and small for an
#: automated flood.
REGISTER_RULE = RateLimitRule(name="auth.register", limit=30, window_seconds=3600)

#: A human mistypes a password a handful of times per hour; 120 attempts per 15
#: minutes leaves room for that while still bounding scrypt work per address.
LOGIN_RULE = RateLimitRule(name="auth.login", limit=120, window_seconds=900)


def subject_for(client: str) -> str:
    """Return the stored, non-reversible subject for a peer address."""

    return hashlib.sha256(client.encode("utf-8")).hexdigest()


def check(
    connection: sqlite3.Connection,
    *,
    rule: RateLimitRule,
    client: str,
    now: float | None = None,
) -> None:
    """Count one request from ``client`` and raise when the budget is spent.

    The counter is incremented even for the request that is refused, so a caller
    cannot keep spending the budget of the next window by retrying in a loop.
    ``now`` is a clock hook for tests; production callers leave it unset.

    The increment, the read-back and the sweep share one transaction, so two
    requests from the same address cannot both read the same count. The caller
    must not already hold a transaction; routes call this before any write.
    """

    timestamp = int(time.time() if now is None else now)
    window_start = timestamp - (timestamp % rule.window_seconds)
    subject = subject_for(client or "unknown")
    expires_at = window_start + rule.window_seconds * STALE_WINDOW_MULTIPLIER

    with db.transaction(connection):
        connection.execute(
            """
            INSERT INTO rate_limit_counters (bucket, subject, window_start, expires_at, count)
            VALUES (?, ?, ?, ?, 1)
            ON CONFLICT (bucket, subject, window_start)
            DO UPDATE SET count = count + 1
            """,
            (rule.name, subject, window_start, expires_at),
        )
        row = connection.execute(
            "SELECT count FROM rate_limit_counters "
            "WHERE bucket = ? AND subject = ? AND window_start = ?",
            (rule.name, subject, window_start),
        ).fetchone()
        count = int(row["count"])
        if count == 1:
            # First request of a fresh window: the previous window for this
            # subject is spent, and rows whose retention elapsed can go too.
            _sweep(connection, rule=rule, subject=subject, window_start=window_start, now=timestamp)

    if count > rule.limit:
        retry_after = window_start + rule.window_seconds - timestamp
        raise RateLimitError(
            f"too many requests to {rule.name.replace('.', ' ')}; try again later.",
            retry_after=retry_after,
        )


def _sweep(
    connection: sqlite3.Connection,
    *,
    rule: RateLimitRule,
    subject: str,
    window_start: int,
    now: int,
) -> None:
    """Drop spent windows for one subject and expired rows for everyone else.

    ``expires_at`` is stored per row, so a sweep triggered by a short-window rule
    cannot remove a longer-window bucket's live counter.
    """

    connection.execute(
        "DELETE FROM rate_limit_counters WHERE bucket = ? AND subject = ? AND window_start <> ?",
        (rule.name, subject, window_start),
    )
    connection.execute("DELETE FROM rate_limit_counters WHERE expires_at < ?", (now,))
