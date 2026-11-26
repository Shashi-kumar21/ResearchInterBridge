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
    Migration(
        version=2,
        name="users_profiles_facts",
        statements=(
            """
            CREATE TABLE IF NOT EXISTS users (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS student_profiles (
                user_id INTEGER PRIMARY KEY REFERENCES users (id) ON DELETE CASCADE,
                full_name TEXT NOT NULL,
                preferred_name TEXT,
                contact_email TEXT NOT NULL,
                phone TEXT,
                institution TEXT,
                department TEXT,
                country TEXT,
                degree_type TEXT,
                current_year INTEGER,
                expected_graduation TEXT,
                gpa REAL,
                gpa_scale REAL,
                coursework_json TEXT NOT NULL DEFAULT '[]',
                skills_json TEXT NOT NULL DEFAULT '[]',
                research_keywords_json TEXT NOT NULL DEFAULT '[]',
                target_countries_json TEXT NOT NULL DEFAULT '[]',
                internship_start TEXT,
                internship_end TEXT,
                funding_required INTEGER,
                consent_given_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS profile_facts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
                field TEXT NOT NULL,
                value TEXT NOT NULL,
                source TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('pending', 'confirmed', 'rejected')),
                confidence REAL,
                created_at TEXT NOT NULL,
                UNIQUE (user_id, field, value, source)
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_profile_facts_user ON profile_facts (user_id, status)",
        ),
    ),
    Migration(
        version=3,
        name="student_documents",
        statements=(
            """
            CREATE TABLE IF NOT EXISTS student_documents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
                kind TEXT NOT NULL CHECK (kind IN ('cv', 'transcript', 'other')),
                display_name TEXT NOT NULL,
                media_type TEXT NOT NULL,
                extension TEXT NOT NULL,
                size_bytes INTEGER NOT NULL CHECK (size_bytes > 0),
                sha256 TEXT NOT NULL,
                storage_path TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'stored'
                    CHECK (status IN ('stored', 'parsed', 'failed')),
                parse_error TEXT,
                uploaded_at TEXT NOT NULL,
                parsed_at TEXT,
                UNIQUE (user_id, sha256)
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_student_documents_user "
            "ON student_documents (user_id, kind)",
            """
            CREATE VIEW IF NOT EXISTS confirmed_profile_facts AS
            SELECT id, user_id, field, value, source, confidence, created_at
            FROM profile_facts
            WHERE status = 'confirmed'
            """,
        ),
    ),
    Migration(
        version=4,
        name="accounts_sessions_consents",
        statements=(
            # Accounts are separate from `users` so anonymous/local rows created by
            # the Phase 2 CLI keep working; one user may have at most one account.
            """
            CREATE TABLE IF NOT EXISTS accounts (
                user_id INTEGER PRIMARY KEY REFERENCES users (id) ON DELETE CASCADE,
                email TEXT NOT NULL UNIQUE COLLATE NOCASE,
                password_hash TEXT NOT NULL,
                failed_attempts INTEGER NOT NULL DEFAULT 0,
                locked_until TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            # Only a SHA-256 of the session token is stored: a database leak must
            # not hand out live sessions.
            """
            CREATE TABLE IF NOT EXISTS sessions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                token_hash TEXT NOT NULL UNIQUE,
                user_id INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
                created_at TEXT NOT NULL,
                last_seen_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                revoked_at TEXT
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions (user_id, expires_at)",
            """
            CREATE TABLE IF NOT EXISTS consents (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
                kind TEXT NOT NULL,
                policy_version TEXT NOT NULL,
                granted_at TEXT NOT NULL,
                UNIQUE (user_id, kind, policy_version)
            )
            """,
        ),
    ),
    Migration(
        version=5,
        name="rate_limit_counters",
        statements=(
            # Fixed-window counters for unauthenticated endpoints. `subject` is a
            # SHA-256 of the peer address, never the address itself: this table is
            # not student data, but a database leak should not become a list of
            # who tried to sign in.
            """
            CREATE TABLE IF NOT EXISTS rate_limit_counters (
                bucket TEXT NOT NULL,
                subject TEXT NOT NULL,
                window_start INTEGER NOT NULL,
                expires_at INTEGER NOT NULL,
                count INTEGER NOT NULL,
                PRIMARY KEY (bucket, subject, window_start)
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_rate_limit_expiry ON rate_limit_counters (expires_at)",
        ),
    ),
    Migration(
        version=6,
        name="research_discovery_cache",
        statements=(
            # Public research data only (ADR-007): these tables have no user_id
            # and are safe to reuse across students. Private rows stay in their
            # own per-user tables.
            """
            CREATE TABLE IF NOT EXISTS institutions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                canonical_key TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                country TEXT,
                website_url TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_institutions_country ON institutions (country, name)",
            """
            CREATE TABLE IF NOT EXISTS departments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                institution_id INTEGER NOT NULL REFERENCES institutions (id) ON DELETE CASCADE,
                canonical_key TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                website_url TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_departments_institution "
            "ON departments (institution_id)",
            # Nullable dates and funding text are deliberate: NULL means "the
            # source did not state it", never "false" or an invented value.
            # `status = 'unverified'` marks a record whose page was not checked.
            """
            CREATE TABLE IF NOT EXISTS programs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                canonical_key TEXT NOT NULL UNIQUE,
                name TEXT NOT NULL,
                institution_id INTEGER REFERENCES institutions (id) ON DELETE SET NULL,
                department_id INTEGER REFERENCES departments (id) ON DELETE SET NULL,
                country TEXT,
                opportunity_type TEXT,
                degree_level TEXT,
                research_areas_json TEXT NOT NULL DEFAULT '[]',
                official_url TEXT NOT NULL,
                eligibility_json TEXT NOT NULL DEFAULT '[]',
                application_method TEXT,
                opening_date TEXT,
                closing_date TEXT,
                start_date TEXT,
                duration_text TEXT,
                stipend_text TEXT,
                tuition_text TEXT,
                accommodation_text TEXT,
                required_documents_json TEXT NOT NULL DEFAULT '[]',
                status TEXT NOT NULL DEFAULT 'unverified'
                    CHECK (status IN ('open', 'upcoming', 'closed', 'unverified')),
                confidence REAL,
                last_checked_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_programs_country_status ON programs (country, status)",
            """
            CREATE TABLE IF NOT EXISTS program_sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                program_id INTEGER NOT NULL REFERENCES programs (id) ON DELETE CASCADE,
                url TEXT NOT NULL,
                publisher TEXT,
                kind TEXT NOT NULL DEFAULT 'official_page',
                fetched_at TEXT NOT NULL,
                verified_at TEXT,
                UNIQUE (program_id, url)
            )
            """,
            # One row per cached entity. `canonical_key` is the shared identity;
            # (entity_type, entity_id) prevents two entries pointing at one row.
            # `raw_payload_path` must stay outside Git.
            """
            CREATE TABLE IF NOT EXISTS research_cache_entries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                entity_type TEXT NOT NULL
                    CHECK (entity_type IN
                        ('institution', 'department', 'program', 'faculty', 'publication')),
                entity_id INTEGER NOT NULL,
                canonical_key TEXT NOT NULL UNIQUE,
                source_url TEXT NOT NULL,
                fetched_at TEXT NOT NULL,
                verified_at TEXT,
                ttl_seconds INTEGER NOT NULL CHECK (ttl_seconds > 0),
                content_hash TEXT NOT NULL,
                http_status INTEGER,
                adapter TEXT NOT NULL,
                parser_version TEXT NOT NULL,
                raw_payload_path TEXT,
                last_attempt_at TEXT NOT NULL,
                last_error TEXT,
                UNIQUE (entity_type, entity_id)
            )
            """,
            """
            CREATE INDEX IF NOT EXISTS idx_cache_entries_type
                ON research_cache_entries (entity_type, verified_at)
            """,
            # A normalised query fingerprint resolves to the ids of the records
            # it produced, so an identical query never reaches the network while
            # its entries are fresh.
            """
            CREATE TABLE IF NOT EXISTS research_query_index (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                fingerprint TEXT NOT NULL UNIQUE,
                entity_type TEXT NOT NULL,
                research_area TEXT,
                country TEXT,
                department TEXT,
                degree_level TEXT,
                entity_ids_json TEXT NOT NULL DEFAULT '[]',
                -- Only set for an empty result set: a "no programs" answer is
                -- cached briefly so a repeated query does not hammer the source.
                -- Non-empty freshness is governed by each record's TTL.
                expires_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
        ),
    ),
    Migration(
        version=7,
        name="faculty_publication_cache",
        statements=(
            # Public research data (ADR-007, ADR-018): no user_id anywhere.
            # Identity is the official profile URL, never the name, so two
            # people with the same name stay two rows (task.md §3.5).
            """
            CREATE TABLE IF NOT EXISTS faculty_members (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                canonical_key TEXT NOT NULL UNIQUE,
                full_name TEXT NOT NULL,
                institution_id INTEGER NOT NULL REFERENCES institutions (id) ON DELETE CASCADE,
                department_id INTEGER REFERENCES departments (id) ON DELETE SET NULL,
                country TEXT,
                title TEXT,
                lab_name TEXT,
                profile_url TEXT NOT NULL,
                research_interests_json TEXT NOT NULL DEFAULT '[]',
                recruitment_evidence_url TEXT,
                confidence REAL,
                last_checked_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_faculty_institution "
            "ON faculty_members (institution_id, department_id)",
            # A contact is only stored with the page that lists it. `verified`
            # means it was listed on the official profile host; anything else is
            # `unverified` and is never sendable.
            """
            CREATE TABLE IF NOT EXISTS faculty_contacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                faculty_id INTEGER NOT NULL REFERENCES faculty_members (id) ON DELETE CASCADE,
                kind TEXT NOT NULL CHECK (kind IN ('email')),
                value TEXT NOT NULL,
                source_url TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('verified', 'unverified')),
                verified_at TEXT,
                UNIQUE (faculty_id, kind)
            )
            """,
            """
            CREATE TABLE IF NOT EXISTS faculty_sources (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                faculty_id INTEGER NOT NULL REFERENCES faculty_members (id) ON DELETE CASCADE,
                url TEXT NOT NULL,
                publisher TEXT,
                kind TEXT NOT NULL DEFAULT 'official_page',
                fetched_at TEXT NOT NULL,
                verified_at TEXT,
                UNIQUE (faculty_id, url)
            )
            """,
            # NULL year/abstract/venue mean "the source did not state it".
            """
            CREATE TABLE IF NOT EXISTS publications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                canonical_key TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                year INTEGER,
                doi TEXT UNIQUE,
                url TEXT NOT NULL,
                venue TEXT,
                abstract TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            # Author names exactly as the source lists them, in order. A name is
            # display text only: it is never used to link a person (§3.5).
            """
            CREATE TABLE IF NOT EXISTS publication_authors (
                publication_id INTEGER NOT NULL REFERENCES publications (id) ON DELETE CASCADE,
                position INTEGER NOT NULL CHECK (position >= 1),
                display_name TEXT NOT NULL,
                PRIMARY KEY (publication_id, position)
            )
            """,
            # The only link between a person and a paper: the source attributed
            # the paper to that profile. Never inferred from an author name.
            """
            CREATE TABLE IF NOT EXISTS faculty_publications (
                faculty_id INTEGER NOT NULL REFERENCES faculty_members (id) ON DELETE CASCADE,
                publication_id INTEGER NOT NULL REFERENCES publications (id) ON DELETE CASCADE,
                linked_at TEXT NOT NULL,
                PRIMARY KEY (faculty_id, publication_id)
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_faculty_publications_publication "
            "ON faculty_publications (publication_id)",
        ),
    ),
    Migration(
        version=8,
        name="faculty_orcid",
        statements=(
            # An ORCID iD stated on the official page. It is the only key later
            # slices may use to fetch a person's papers from a scholarly index,
            # because a name search would merge people (task.md §3.5). NULL means
            # the page did not state one.
            "ALTER TABLE faculty_members ADD COLUMN orcid TEXT",
            "CREATE INDEX IF NOT EXISTS idx_faculty_orcid ON faculty_members (orcid)",
        ),
    ),
    Migration(
        version=9,
        name="faculty_department_index",
        statements=(
            # The department browse row counts faculty by department_id alone;
            # idx_faculty_institution leads with institution_id and cannot serve
            # that lookup (QA n2 on PR #29).
            "CREATE INDEX IF NOT EXISTS idx_faculty_department ON faculty_members (department_id)",
        ),
    ),
    Migration(
        version=10,
        name="shortlist_and_match_scores",
        statements=(
            # Per-user, never shared (task.md §13.4): one row per student and
            # cached target. UNIQUE is the deduplication rule; an excluded
            # target is remembered so browse screens can hide it.
            """
            CREATE TABLE IF NOT EXISTS shortlist_items (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
                target_type TEXT NOT NULL CHECK (target_type IN ('faculty', 'program')),
                target_id INTEGER NOT NULL CHECK (target_id > 0),
                status TEXT NOT NULL CHECK (status IN ('shortlisted', 'excluded')),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE (user_id, target_type, target_id)
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_shortlist_user ON shortlist_items (user_id, status)",
            # A snapshot of the explained score at the moment it was shortlisted
            # or refreshed (ADR-022). The result JSON holds factors, gates and
            # gaps; factor `matched` terms and reasons can quote the student's own
            # keywords, skills and internship dates, so the table is per-user,
            # cascade-deleted and included in the account export.
            """
            CREATE TABLE IF NOT EXISTS match_scores (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
                shortlist_item_id INTEGER NOT NULL
                    REFERENCES shortlist_items (id) ON DELETE CASCADE,
                score INTEGER NOT NULL CHECK (score BETWEEN 0 AND 100),
                eligibility TEXT NOT NULL
                    CHECK (eligibility IN ('eligible', 'ineligible', 'unknown', 'not_assessed')),
                scoring_version TEXT NOT NULL,
                inputs_fingerprint TEXT NOT NULL,
                evaluated_on TEXT NOT NULL,
                result_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_match_scores_item "
            "ON match_scores (shortlist_item_id, id)",
        ),
    ),
    Migration(
        version=11,
        name="result_set_reuse",
        statements=(
            # task.md §13.4 / ADR-023. One row per student: the bucket their
            # derived, non-identifying features fall in. Per-user, cascade on
            # account deletion; only counted, never read for another student.
            """
            CREATE TABLE IF NOT EXISTS profile_buckets (
                user_id INTEGER PRIMARY KEY REFERENCES users (id) ON DELETE CASCADE,
                bucket_id TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """,
            "CREATE INDEX IF NOT EXISTS idx_profile_buckets_bucket ON profile_buckets (bucket_id)",
            # Shared and public: the candidate ids of public cache records for a
            # bucket and query. Built from bucket features only, never from one
            # student's profile, and holding no scores.
            """
            CREATE TABLE IF NOT EXISTS bucket_result_sets (
                bucket_id TEXT NOT NULL,
                target_type TEXT NOT NULL CHECK (target_type IN ('faculty', 'program')),
                query_fingerprint TEXT NOT NULL,
                candidate_ids_json TEXT NOT NULL,
                truncated INTEGER NOT NULL CHECK (truncated IN (0, 1)),
                created_at TEXT NOT NULL,
                expires_at TEXT NOT NULL,
                PRIMARY KEY (bucket_id, target_type, query_fingerprint)
            )
            """,
        ),
    ),
    Migration(
        version=12,
        name="cv_versions",
        statements=(
            # task.md §3.7 / ADR-024. Per-user, never shared. The original is the
            # CV as uploaded, split into sections; later versions are tailored to
            # one target and only exist after the student approves them.
            """
            CREATE TABLE IF NOT EXISTS cv_versions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
                document_id INTEGER NOT NULL
                    REFERENCES student_documents (id) ON DELETE CASCADE,
                kind TEXT NOT NULL CHECK (kind IN ('original', 'tailored')),
                parent_version_id INTEGER REFERENCES cv_versions (id) ON DELETE CASCADE,
                target_type TEXT CHECK (target_type IN ('faculty', 'program')),
                target_id INTEGER,
                sections_json TEXT NOT NULL,
                content_sha256 TEXT NOT NULL,
                created_at TEXT NOT NULL,
                approved_at TEXT,
                CHECK (
                    (kind = 'original' AND parent_version_id IS NULL AND target_type IS NULL
                        AND target_id IS NULL)
                    OR (kind = 'tailored' AND parent_version_id IS NOT NULL
                        AND target_type IS NOT NULL AND target_id IS NOT NULL
                        AND approved_at IS NOT NULL)
                )
            )
            """,
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_cv_versions_one_original "
            "ON cv_versions (document_id) WHERE kind = 'original'",
            "CREATE INDEX IF NOT EXISTS idx_cv_versions_user ON cv_versions (user_id, kind)",
            # The original is the student's source document: never silently
            # modified (§3.7). The database itself refuses an update.
            """
            CREATE TRIGGER IF NOT EXISTS cv_versions_original_is_immutable
            BEFORE UPDATE ON cv_versions
            WHEN OLD.kind = 'original'
            BEGIN
                SELECT RAISE(ABORT, 'the original CV version cannot be modified');
            END
            """,
        ),
    ),
    Migration(
        version=13,
        name="cv_suggestions",
        statements=(
            # task.md §3.7 / ADR-024. Per-user. A run is one rule pass of one CV
            # version against one target; each suggestion stays pending until
            # the student accepts, rejects or edits it (Phase 7c).
            """
            CREATE TABLE IF NOT EXISTS cv_analysis_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                user_id INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
                version_id INTEGER NOT NULL REFERENCES cv_versions (id) ON DELETE CASCADE,
                target_type TEXT NOT NULL CHECK (target_type IN ('faculty', 'program')),
                target_id INTEGER NOT NULL CHECK (target_id > 0),
                rules_version TEXT NOT NULL,
                inputs_fingerprint TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
            """,
            # One run per identical input set: a repeat or a race reuses it.
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_cv_runs_inputs ON cv_analysis_runs "
            "(user_id, version_id, target_type, target_id, inputs_fingerprint)",
            """
            CREATE TABLE IF NOT EXISTS cv_suggestions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id INTEGER NOT NULL REFERENCES cv_analysis_runs (id) ON DELETE CASCADE,
                user_id INTEGER NOT NULL REFERENCES users (id) ON DELETE CASCADE,
                position INTEGER NOT NULL,
                kind TEXT NOT NULL CHECK (
                    kind IN ('reorder_sections', 'reorder_bullets', 'surface_skill', 'flag_vague')
                ),
                payload_json TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending'
                    CHECK (status IN ('pending', 'accepted', 'rejected', 'edited')),
                edited_text TEXT,
                updated_at TEXT NOT NULL,
                UNIQUE (run_id, position)
            )
            """,
        ),
    ),
    Migration(
        version=14,
        name="cv_tailored_unique",
        statements=(
            # One tailored version per parent, target and content: a repeated or
            # concurrent approval of the same choices reuses it (QA n2 on #46).
            "CREATE UNIQUE INDEX IF NOT EXISTS idx_cv_versions_tailored_unique "
            "ON cv_versions (parent_version_id, target_type, target_id, content_sha256) "
            "WHERE kind = 'tailored'",
        ),
    ),
)


def utc_now() -> str:
    """Return the current UTC time as an ISO-8601 string with microseconds."""

    return datetime.now(UTC).isoformat(timespec="microseconds")


@contextmanager
def transaction(connection: sqlite3.Connection) -> Iterator[None]:
    """Run a block inside an explicit ``BEGIN IMMEDIATE`` transaction.

    Connections are opened in autocommit mode, so callers that must keep a state
    change and its audit row atomic use this context manager. It must not be used
    when the caller already holds a transaction.
    """

    connection.execute("BEGIN IMMEDIATE")
    try:
        yield
    except BaseException:
        connection.execute("ROLLBACK")
        raise
    else:
        connection.execute("COMMIT")


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
