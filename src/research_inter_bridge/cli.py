"""Command line entry point: ``python -m research_inter_bridge``.

Commands:

* ``migrate`` - create or upgrade the SQLite schema.
* ``health``  - print a JSON health report and exit non-zero when unhealthy.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from . import db
from .config import load_settings
from .errors import ConfigError, MigrationError
from .health import check_health
from .logging import configure_logging, get_logger

logger = get_logger(__name__)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="research-inter-bridge",
        description="ResearchInterBridge command line interface.",
    )
    parser.add_argument(
        "--env-file",
        type=Path,
        default=Path(".env"),
        help="Optional .env file; real environment variables always win (default: .env).",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("migrate", help="Apply pending database migrations.")
    health = subparsers.add_parser("health", help="Check database reachability and schema.")
    health.add_argument(
        "--json",
        action="store_true",
        help="Emit machine-readable JSON (already the default output).",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the CLI and return a process exit code."""

    args = _build_parser().parse_args(argv)
    try:
        settings = load_settings(env_file=args.env_file)
    except ConfigError as exc:
        print(f"configuration error: {exc}", file=sys.stderr)
        return 2

    configure_logging(settings.log_level)

    if args.command == "migrate":
        try:
            with db.session(settings.database_path) as connection:
                version = db.apply_migrations(connection)
                db.record_audit_event(
                    connection,
                    actor="cli",
                    action="database.migrate",
                    outcome="success",
                    target_type="schema",
                    target_id=str(version),
                )
        except (MigrationError, OSError) as exc:
            print(f"migration failed: {exc}", file=sys.stderr)
            return 1
        print(json.dumps({"schema_version": version}, indent=2))
        return 0

    report = check_health(settings)
    print(json.dumps(report.to_dict(), indent=2))
    return 0 if report.ok else 1


if __name__ == "__main__":  # pragma: no cover - exercised via __main__ module
    raise SystemExit(main())
