"""SQLite access, schema migrations and the audit-event store.

The database layer is intentionally thin: explicit SQL, explicit migrations,
explicit errors. Every write that matters to the product must be recorded in
``audit_events`` so a student (or QA) can trace what happened.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .errors import MigrationError
from .logging import get_logger, redact_mapping

logger = get_logger(__name__)


@dataclass(frozen=True, slots=True)
class Migration:
    """A single forward-only schema migration."""

    version: int
    name: str
    statements: tuple[str, ...]


#: Ordered, contiguous, forward-only migrations. Never edit an applied migration;
#: add a new one instead.
MIGRATIONS: tuple[Migration, ...] = (
    Migration(
        version=1,
        name="audit_events",
        statements=(
            """
            CREATE TABLE IF NOT EXISTS audit_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                occurred_at TEXT NOT NULL,
                actor TEXT NOT NULL,
                action TEXT NOT NULL,
                target_type TEXT,
                target_id TEXT,
                outcome TEXT NOT NULL,
                metadata_json TEXT NOT NULL DEFAULT '{}'
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_audit_events_occurred_at ON audit_events (occurred_at)",
            "CREATE INDEX IF NOT EXISTS idx_audit_events_action ON audit_events (action)",
        ),
    ),
)


def utc_now() -> str:
    """Return the current UTC time as an ISO-8601 string with microseconds."""

    return datetime.now(UTC).isoformat(timespec="microseconds")


def validate_migrations(migrations: Sequence[Migration] = MIGRATIONS) -> None:
    """Ensure migration versions start at 1 and have no gaps or duplicates."""

    expected = 1
    seen: set[int] = set()
    for migration in migrations:
        if migration.version in seen:
            raise MigrationError(f"Duplicate migration version {migration.version}.")
        if migration.version != expected:
            raise MigrationError(
                f"Migration versions must be contiguous starting at 1; "
                f"expected {expected}, found {migration.version} ({migration.name})."
            )
        if not migration.statements:
            raise MigrationError(f"Migration {migration.version} ({migration.name}) is empty.")
        seen.add(migration.version)
        expected += 1


def connect(database_path: Path | str) -> sqlite3.Connection:
    """Open a SQLite connection with the pragmas this project relies on.

    ``check_same_thread=False`` lets the future worker/scheduler share the
    connection pool, and ``busy_timeout`` keeps concurrent writers from failing
    immediately (needed for the university daily-cap counters later).

    ``isolation_level=None`` disables :mod:`sqlite3`'s implicit transactions so
    this module controls transaction boundaries explicitly with ``BEGIN``.
    """

    path = Path(database_path).expanduser()
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(
        str(path),
        timeout=30.0,
        check_same_thread=False,
        isolation_level=None,
    )
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = WAL")
    connection.execute("PRAGMA busy_timeout = 30000")
    return connection


@contextmanager
def session(database_path: Path | str) -> Iterator[sqlite3.Connection]:
    """Context manager yielding a connection and closing it afterwards."""

    connection = connect(database_path)
    try:
        yield connection
    finally:
        connection.close()


def connect_readonly(database_path: Path | str) -> sqlite3.Connection:
    """Open an existing SQLite file read-only.

    Used by health probes: a readiness check must never create tables, touch the
    journal mode or otherwise write to the database (QA m3). A missing file is
    reported as an actionable :class:`FileNotFoundError` instead of being created.
    """

    path = Path(database_path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(
            f"database file {path} does not exist; "
            "run `python -m research_inter_bridge migrate` first."
        )
    connection = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=5.0)
    connection.row_factory = sqlite3.Row
    return connection


def schema_version(connection: sqlite3.Connection) -> int:
    """Return the applied migration version without modifying the schema.

    Unlike :func:`current_version` this never creates the bookkeeping table, so
    it is safe on read-only connections.
    """

    exists = connection.execute(
        "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'schema_migrations'"
    ).fetchone()
    if exists is None:
        return 0
    row = connection.execute("SELECT COALESCE(MAX(version), 0) AS version FROM schema_migrations")
    return int(row.fetchone()["version"])


def current_version(connection: sqlite3.Connection) -> int:
    """Return the highest applied migration version, creating the ledger if absent."""

    connection.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "version INTEGER PRIMARY KEY, "
        "name TEXT NOT NULL, "
        "applied_at TEXT NOT NULL)"
    )
    return schema_version(connection)


def apply_migrations(
    connection: sqlite3.Connection,
    migrations: Sequence[Migration] = MIGRATIONS,
) -> int:
    """Apply pending migrations and return the resulting schema version.

    Each migration runs inside its own transaction so a failure leaves the
    database on the last known-good version instead of a partial schema.
    """

    validate_migrations(migrations)
    version = current_version(connection)
    for migration in migrations:
        if migration.version <= version:
            continue
        try:
            connection.execute("BEGIN IMMEDIATE")
            for statement in migration.statements:
                connection.execute(statement)
            connection.execute(
                "INSERT INTO schema_migrations (version, name, applied_at) VALUES (?, ?, ?)",
                (migration.version, migration.name, utc_now()),
            )
            connection.commit()
        except sqlite3.Error as exc:
            connection.rollback()
            raise MigrationError(
                f"Migration {migration.version} ({migration.name}) failed: {exc}. "
                "The database was left at version "
                f"{version}; fix the migration before retrying."
            ) from exc
        version = migration.version
        logger.info("applied migration version=%s name=%s", migration.version, migration.name)
    return version


def latest_version(migrations: Sequence[Migration] = MIGRATIONS) -> int:
    """Return the newest migration version defined in code."""

    return max((migration.version for migration in migrations), default=0)


def record_audit_event(
    connection: sqlite3.Connection,
    *,
    actor: str,
    action: str,
    outcome: str,
    target_type: str | None = None,
    target_id: str | None = None,
    metadata: Mapping[str, Any] | None = None,
) -> int:
    """Append an audit event and return its id.

    ``metadata`` is redacted before it is persisted: audit rows must never
    contain tokens or private document text.

    The caller owns the transaction. Connections are opened with
    ``isolation_level=None``, so outside an explicit ``BEGIN`` the insert
    autocommits; inside one it joins the caller's transaction and a rollback
    removes it too (state change + audit row stay atomic).
    """

    if not actor or not action or not outcome:
        raise ValueError("actor, action and outcome are required for an audit event.")
    safe_metadata = redact_mapping(metadata or {})
    cursor = connection.execute(
        "INSERT INTO audit_events "
        "(occurred_at, actor, action, target_type, target_id, outcome, metadata_json) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (
            utc_now(),
            actor,
            action,
            target_type,
            target_id,
            outcome,
            json.dumps(safe_metadata, sort_keys=True, ensure_ascii=False),
        ),
    )
    return int(cursor.lastrowid or 0)


@dataclass(frozen=True, slots=True)
class AuditEvent:
    """A persisted audit event, decoded for read-back and testing."""

    id: int
    occurred_at: str
    actor: str
    action: str
    outcome: str
    target_type: str | None
    target_id: str | None
    metadata: dict[str, Any]


def list_audit_events(connection: sqlite3.Connection, *, limit: int = 50) -> list[AuditEvent]:
    """Return the most recent audit events, newest first."""

    if limit <= 0:
        raise ValueError("limit must be positive.")
    rows = connection.execute(
        "SELECT id, occurred_at, actor, action, outcome, target_type, target_id, metadata_json "
        "FROM audit_events ORDER BY id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [
        AuditEvent(
            id=int(row["id"]),
            occurred_at=str(row["occurred_at"]),
            actor=str(row["actor"]),
            action=str(row["action"]),
            outcome=str(row["outcome"]),
            target_type=row["target_type"],
            target_id=row["target_id"],
            metadata=json.loads(row["metadata_json"]),
        )
        for row in rows
    ]
