"""Health checks used by the CLI, CI smoke tests and future readiness probes.

A health check is a *probe*: it must never create tables, change the journal
mode or otherwise write to the database (QA m3). It therefore opens the file
read-only and only reports what it can observe.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import db
from .config import Settings


@dataclass(frozen=True, slots=True)
class HealthReport:
    """Outcome of every health check, plus the values an operator needs."""

    ok: bool
    schema_version: int
    expected_schema_version: int
    database_path: str
    checks: dict[str, bool] = field(default_factory=dict)
    details: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "ok": self.ok,
            "schema_version": self.schema_version,
            "expected_schema_version": self.expected_schema_version,
            "database_path": self.database_path,
            "checks": dict(self.checks),
            "details": dict(self.details),
        }


def check_health(settings: Settings) -> HealthReport:
    """Verify the database is readable and migrated to the latest version."""

    path = Path(settings.resolved_database_path)
    expected = db.latest_version()
    checks: dict[str, bool] = {"database_reachable": False, "schema_current": False}
    details: dict[str, str] = {}
    version = 0

    try:
        connection = db.connect_readonly(path)
    except (sqlite3.Error, OSError) as exc:
        details["database_error"] = str(exc)
        checks["database_reachable"] = False
    else:
        try:
            connection.execute("SELECT 1")
            checks["database_reachable"] = True
            version = db.schema_version(connection)
        except sqlite3.Error as exc:
            details["database_error"] = str(exc)
        finally:
            connection.close()

    if checks["database_reachable"]:
        checks["schema_current"] = version >= expected
        if not checks["schema_current"]:
            details["schema_hint"] = "Run `python -m research_inter_bridge migrate`."

    return HealthReport(
        ok=all(checks.values()),
        schema_version=version,
        expected_schema_version=expected,
        database_path=str(path),
        checks=checks,
        details=details,
    )
