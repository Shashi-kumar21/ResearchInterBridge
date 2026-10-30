from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from research_inter_bridge import db
from research_inter_bridge.errors import MigrationError
from research_inter_bridge.logging import REDACTED


def _table_names(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    return {str(row["name"]) for row in rows}


def test_connect_creates_parent_directory_and_pragmas(tmp_path: Path) -> None:
    database_path = tmp_path / "nested" / "app.db"

    with db.session(database_path) as connection:
        assert database_path.exists()
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1
        assert connection.execute("PRAGMA busy_timeout").fetchone()[0] == 30000


def test_fresh_database_has_version_zero(tmp_path: Path) -> None:
    with db.session(tmp_path / "app.db") as connection:
        assert db.current_version(connection) == 0
        assert "schema_migrations" in _table_names(connection)


def test_apply_migrations_creates_schema_and_records_version(tmp_path: Path) -> None:
    with db.session(tmp_path / "app.db") as connection:
        version = db.apply_migrations(connection)

        assert version == db.latest_version()
        assert version >= 1
        assert {"audit_events", "schema_migrations"} <= _table_names(connection)
        row = connection.execute(
            "SELECT name FROM schema_migrations WHERE version = ?", (version,)
        ).fetchone()
        assert row["name"] == db.MIGRATIONS[version - 1].name


def test_apply_migrations_is_idempotent(tmp_path: Path) -> None:
    with db.session(tmp_path / "app.db") as connection:
        first = db.apply_migrations(connection)
        second = db.apply_migrations(connection)

        assert first == second
        count = connection.execute("SELECT COUNT(*) AS c FROM schema_migrations").fetchone()["c"]
        assert count == first


def test_migration_versions_must_be_contiguous() -> None:
    with pytest.raises(MigrationError, match="contiguous"):
        db.validate_migrations(
            [
                db.Migration(version=1, name="a", statements=("SELECT 1",)),
                db.Migration(version=3, name="c", statements=("SELECT 1",)),
            ]
        )


def test_migration_versions_must_be_unique() -> None:
    with pytest.raises(MigrationError, match="Duplicate"):
        db.validate_migrations(
            [
                db.Migration(version=1, name="a", statements=("SELECT 1",)),
                db.Migration(version=1, name="b", statements=("SELECT 1",)),
            ]
        )


def test_empty_migration_is_rejected() -> None:
    with pytest.raises(MigrationError, match="empty"):
        db.validate_migrations([db.Migration(version=1, name="a", statements=())])


def test_failed_migration_leaves_previous_version_and_reports_hint(tmp_path: Path) -> None:
    migrations = (
        db.Migration(version=1, name="ok", statements=("CREATE TABLE ok (id INTEGER)",)),
        db.Migration(version=2, name="broken", statements=("CREATE TABLE broken (",)),
    )

    with db.session(tmp_path / "app.db") as connection:
        with pytest.raises(MigrationError) as excinfo:
            db.apply_migrations(connection, migrations)

        assert "broken" in str(excinfo.value)
        assert "left at version 1" in str(excinfo.value)
        assert db.current_version(connection) == 1
        assert "ok" in _table_names(connection)
        assert "broken" not in _table_names(connection)


def test_record_and_list_audit_events(tmp_path: Path) -> None:
    with db.session(tmp_path / "app.db") as connection:
        db.apply_migrations(connection)
        first = db.record_audit_event(
            connection,
            actor="student:1",
            action="draft.created",
            outcome="success",
            target_type="email_draft",
            target_id="17",
            metadata={"professor_id": 5, "attempt": 1},
        )
        second = db.record_audit_event(
            connection,
            actor="cli",
            action="database.migrate",
            outcome="success",
        )

        events = db.list_audit_events(connection)

    assert (first, second) == (1, 2)
    assert [event.action for event in events] == ["database.migrate", "draft.created"]
    assert events[1].metadata == {"professor_id": 5, "attempt": 1}
    assert events[1].target_type == "email_draft"
    assert events[0].target_type is None


def test_audit_metadata_is_redacted_before_persisting(tmp_path: Path) -> None:
    with db.session(tmp_path / "app.db") as connection:
        db.apply_migrations(connection)
        db.record_audit_event(
            connection,
            actor="student:1",
            action="oauth.connect",
            outcome="success",
            metadata={"access_token": "ya29.secret-value", "scope": "gmail.compose"},
        )

        event = db.list_audit_events(connection)[0]

    assert event.metadata["access_token"] == REDACTED
    assert event.metadata["scope"] == "gmail.compose"
    with db.session(tmp_path / "app.db") as connection:
        raw = connection.execute("SELECT metadata_json FROM audit_events").fetchone()[0]
    assert "ya29.secret-value" not in raw


def test_audit_event_requires_actor_action_and_outcome(tmp_path: Path) -> None:
    with db.session(tmp_path / "app.db") as connection:
        db.apply_migrations(connection)
        with pytest.raises(ValueError, match="required"):
            db.record_audit_event(connection, actor="", action="x", outcome="success")


def test_list_audit_events_rejects_non_positive_limit(tmp_path: Path) -> None:
    with db.session(tmp_path / "app.db") as connection:
        db.apply_migrations(connection)
        with pytest.raises(ValueError, match="positive"):
            db.list_audit_events(connection, limit=0)
