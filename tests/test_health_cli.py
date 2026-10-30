from __future__ import annotations

import json
from pathlib import Path

from research_inter_bridge import db
from research_inter_bridge.cli import main
from research_inter_bridge.config import Settings
from research_inter_bridge.health import check_health


def test_health_fails_before_migration(tmp_path: Path) -> None:
    settings = Settings(database_path=tmp_path / "app.db")

    report = check_health(settings)

    assert report.ok is False
    assert report.checks["database_reachable"] is True
    assert report.checks["schema_current"] is False
    assert report.schema_version == 0
    assert "migrate" in report.details["schema_hint"]


def test_health_passes_after_migration(tmp_path: Path) -> None:
    settings = Settings(database_path=tmp_path / "app.db")
    with db.session(settings.database_path) as connection:
        db.apply_migrations(connection)

    report = check_health(settings)

    assert report.ok is True
    assert report.schema_version == db.latest_version()
    assert report.expected_schema_version == db.latest_version()
    assert all(report.checks.values())
    assert report.details == {}
    assert report.database_path.endswith("app.db")


def test_health_reports_unusable_path_without_raising(tmp_path: Path) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    settings = Settings(database_path=blocker / "app.db")

    report = check_health(settings)

    assert report.ok is False
    assert report.checks["database_reachable"] is False
    assert "filesystem_error" in report.details


def test_cli_migrate_then_health_exits_zero(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("RIB_DATABASE_PATH", str(tmp_path / "cli.db"))
    env_file = str(tmp_path / "absent.env")

    assert main(["--env-file", env_file, "migrate"]) == 0
    migrate_payload = json.loads(capsys.readouterr().out)
    assert migrate_payload == {"schema_version": db.latest_version()}

    assert main(["--env-file", env_file, "health"]) == 0
    health_payload = json.loads(capsys.readouterr().out)
    assert health_payload["ok"] is True
    assert health_payload["schema_version"] == db.latest_version()


def test_cli_health_exits_one_when_schema_missing(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("RIB_DATABASE_PATH", str(tmp_path / "unmigrated.db"))

    assert main(["--env-file", str(tmp_path / "absent.env"), "health"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["checks"]["schema_current"] is False


def test_cli_reports_configuration_error(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("RIB_LOG_LEVEL", "loud")

    exit_code = main(["--env-file", str(tmp_path / "absent.env"), "health"])
    captured = capsys.readouterr()

    assert exit_code == 2
    assert "RIB_LOG_LEVEL" in captured.err


def test_cli_migrate_reports_unusable_path_without_traceback(
    tmp_path: Path, monkeypatch, capsys
) -> None:
    blocker = tmp_path / "blocker"
    blocker.write_text("not a directory", encoding="utf-8")
    monkeypatch.setenv("RIB_DATABASE_PATH", str(blocker / "app.db"))

    exit_code = main(["--env-file", str(tmp_path / "absent.env"), "migrate"])
    captured = capsys.readouterr()

    assert exit_code == 1
    assert captured.err.startswith("migration failed:")
    assert "Traceback" not in captured.err


def test_cli_migrate_records_audit_event(tmp_path: Path, monkeypatch, capsys) -> None:
    database_path = tmp_path / "env.db"
    monkeypatch.setenv("RIB_DATABASE_PATH", str(database_path))

    assert main(["--env-file", str(tmp_path / "absent.env"), "migrate"]) == 0
    capsys.readouterr()

    with db.session(database_path) as connection:
        events = db.list_audit_events(connection)

    assert [(event.actor, event.action, event.outcome) for event in events] == [
        ("cli", "database.migrate", "success")
    ]
