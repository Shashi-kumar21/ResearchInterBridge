"""Command line entry point: ``python -m research_inter_bridge``.

Commands:

* ``migrate``          - create or upgrade the SQLite schema.
* ``health``           - print a JSON health report and exit non-zero when unhealthy.
* ``user-create``      - create an empty local user.
* ``document-add``     - validate, store and optionally parse a CV/transcript.
* ``document-parse``   - parse a stored document and extract candidate facts.
* ``document-list``    - list a user's documents.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
from collections.abc import Sequence
from pathlib import Path

from . import db, documents, facts, parsing, profiles
from .config import Settings, load_settings
from .errors import ConfigError, DocumentError, MigrationError, ProfileError
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

    add = subparsers.add_parser("document-add", help="Validate, store and optionally parse a CV.")
    add.add_argument("--user-id", type=int, required=True)
    add.add_argument("--file", type=Path, required=True, help="PDF, DOCX or TXT to upload.")
    add.add_argument(
        "--kind",
        choices=documents.DOCUMENT_KINDS,
        default="cv",
        help="Document kind (default: cv).",
    )
    add.add_argument(
        "--parse",
        action="store_true",
        help="Extract text and candidate facts immediately after storing.",
    )

    parse = subparsers.add_parser("document-parse", help="Parse a stored document.")
    parse.add_argument("--user-id", type=int, required=True)
    parse.add_argument("--document-id", type=int, required=True)

    listing = subparsers.add_parser("document-list", help="List a user's stored documents.")
    listing.add_argument("--user-id", type=int, required=True)

    users = subparsers.add_parser("user-create", help="Create an empty local user.")
    users.add_argument("--json", action="store_true", help="Emit JSON (already the default).")

    fact_list = subparsers.add_parser("fact-list", help="Show facts awaiting confirmation.")
    fact_list.add_argument("--user-id", type=int, required=True)

    fact_confirm = subparsers.add_parser("fact-confirm", help="Confirm one extracted fact.")
    fact_confirm.add_argument("--user-id", type=int, required=True)
    fact_confirm.add_argument("--fact-id", type=int, required=True)
    fact_confirm.add_argument(
        "--value", help="Corrected value to store instead of the extracted one."
    )

    fact_reject = subparsers.add_parser("fact-reject", help="Reject one extracted fact.")
    fact_reject.add_argument("--user-id", type=int, required=True)
    fact_reject.add_argument("--fact-id", type=int, required=True)

    fact_apply = subparsers.add_parser(
        "fact-apply", help="Copy confirmed facts into the student profile."
    )
    fact_apply.add_argument("--user-id", type=int, required=True)
    fact_apply.add_argument(
        "--overwrite",
        action="store_true",
        help="Replace values the student already typed (default: only fill blanks).",
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
        except (MigrationError, OSError, sqlite3.Error) as exc:
            print(f"migration failed: {exc}", file=sys.stderr)
            return 1
        print(json.dumps({"schema_version": version}, indent=2))
        return 0

    if args.command == "user-create":
        try:
            with db.session(settings.database_path) as connection:
                db.apply_migrations(connection)
                user_id = profiles.create_user(connection)
                db.record_audit_event(
                    connection,
                    actor="cli",
                    action="user.created",
                    outcome="success",
                    target_type="user",
                    target_id=str(user_id),
                )
        except (OSError, sqlite3.Error) as exc:
            print(f"user creation failed: {exc}", file=sys.stderr)
            return 1
        print(json.dumps({"user_id": user_id}, indent=2))
        return 0

    if args.command.startswith("document-"):
        try:
            return _run_document_command(args, settings)
        except DocumentError as exc:
            print(f"document error: {exc}", file=sys.stderr)
            return 1
        except (OSError, sqlite3.Error) as exc:
            print(f"document operation failed: {exc}", file=sys.stderr)
            return 1

    if args.command.startswith("fact-"):
        try:
            return _run_fact_command(args, settings)
        except ProfileError as exc:
            print(f"fact error: {exc}", file=sys.stderr)
            return 1
        except (OSError, sqlite3.Error) as exc:
            print(f"fact operation failed: {exc}", file=sys.stderr)
            return 1

    report = check_health(settings)
    print(json.dumps(report.to_dict(), indent=2))
    return 0 if report.ok else 1


def _document_payload(record: documents.DocumentRecord) -> dict[str, object]:
    return {
        "id": record.id,
        "kind": record.kind,
        "display_name": record.display_name,
        "media_type": record.media_type,
        "size_bytes": record.size_bytes,
        "status": record.status,
        "sha256": record.sha256,
        "uploaded_at": record.uploaded_at,
        "parse_error": record.parse_error,
    }


def _run_document_command(args: argparse.Namespace, settings: Settings) -> int:
    """Run one document subcommand; document errors are handled by the caller."""

    with db.session(settings.database_path) as connection:
        db.apply_migrations(connection)
        if args.command == "document-list":
            records = documents.list_documents(connection, args.user_id)
            print(json.dumps({"documents": [_document_payload(r) for r in records]}, indent=2))
            return 0

        if args.command == "document-parse":
            result = parsing.parse_document(
                connection,
                document_id=args.document_id,
                user_id=args.user_id,
                data_directory=settings.data_directory,
                actor=f"student:{args.user_id}",
            )
            print(
                json.dumps(
                    {
                        "document": _document_payload(result.document),
                        "characters": result.character_count,
                        "facts_extracted": len(result.fact_ids),
                    },
                    indent=2,
                )
            )
            return 0

        payload = Path(args.file).read_bytes()
        record = documents.save_document(
            connection,
            user_id=args.user_id,
            display_name=Path(args.file).name,
            payload=payload,
            data_directory=settings.data_directory,
            kind=args.kind,
            actor=f"student:{args.user_id}",
        )
        output: dict[str, object] = {"document": _document_payload(record)}
        if args.parse:
            result = parsing.parse_document(
                connection,
                document_id=record.id,
                user_id=args.user_id,
                data_directory=settings.data_directory,
                actor=f"student:{args.user_id}",
            )
            output["document"] = _document_payload(result.document)
            output["characters"] = result.character_count
            output["facts_extracted"] = len(result.fact_ids)
        print(json.dumps(output, indent=2))
        return 0


if __name__ == "__main__":  # pragma: no cover - exercised via __main__ module
    raise SystemExit(main())


def _run_fact_command(args: argparse.Namespace, settings: Settings) -> int:
    """Run one fact subcommand; profile/fact errors are handled by the caller."""

    with db.session(settings.database_path) as connection:
        db.apply_migrations(connection)
        actor = f"student:{args.user_id}"

        if args.command == "fact-list":
            groups = [
                {
                    "field": group.field_name,
                    "has_conflict": group.has_conflict,
                    "pending": [
                        {
                            "id": candidate.id,
                            "value": candidate.value,
                            "source": candidate.source,
                            "confidence": candidate.confidence,
                        }
                        for candidate in group.pending
                    ],
                    "confirmed": [
                        {"id": candidate.id, "value": candidate.value}
                        for candidate in group.confirmed
                    ],
                }
                for group in facts.fact_groups(connection, args.user_id)
            ]
            print(json.dumps({"fields": groups}, indent=2))
            return 0

        if args.command == "fact-confirm":
            confirmed = facts.confirm_fact(
                connection,
                user_id=args.user_id,
                fact_id=args.fact_id,
                actor=actor,
                corrected_value=args.value,
            )
            if not confirmed:
                print(f"fact error: fact {args.fact_id} does not exist.", file=sys.stderr)
                return 1
            print(json.dumps({"confirmed": args.fact_id}, indent=2))
            return 0

        if args.command == "fact-reject":
            rejected = facts.reject_fact(
                connection, user_id=args.user_id, fact_id=args.fact_id, actor=actor
            )
            if not rejected:
                print(f"fact error: fact {args.fact_id} does not exist.", file=sys.stderr)
                return 1
            print(json.dumps({"rejected": args.fact_id}, indent=2))
            return 0

        applied = facts.apply_confirmed_facts_to_profile(
            connection, user_id=args.user_id, actor=actor, overwrite=args.overwrite
        )
        print(json.dumps({"applied": list(applied)}, indent=2))
        return 0
