"""Consent texts, versions and the consent ledger.

Kept in its own module so both :mod:`auth` (registration) and :mod:`documents`
(before storing or parsing a file) can enforce the same rule without an import
cycle.

Bumping :data:`POLICY_VERSION` invalidates every earlier consent for the affected
kind: the product asks again instead of assuming an old acceptance still applies.
"""

from __future__ import annotations

import sqlite3

from . import db
from .errors import AuthError, ConsentError

POLICY_VERSION = "2026-10-31"

CONSENT_TEXTS: dict[str, str] = {
    "personal_data": (
        "Store my profile, uploaded CV/transcripts and derived facts on this "
        "instance so the product can match me with research groups."
    ),
    "document_processing": (
        "Extract text and candidate facts from my documents on this instance; "
        "only facts I confirm are ever used in outreach."
    ),
    "gmail_access": (
        "Connect my Gmail account later with read/compose/send scopes so I can "
        "send approved emails and detect replies."
    ),
}

#: Consents required before an account may store documents.
REQUIRED_CONSENTS: frozenset[str] = frozenset({"personal_data", "document_processing"})


def _validate_kind(kind: str) -> str:
    if kind not in CONSENT_TEXTS:
        raise ConsentError(
            f"unknown consent kind {kind!r}; known: {', '.join(sorted(CONSENT_TEXTS))}.",
            code="unknown_consent",
        )
    return kind


def has_consent(connection: sqlite3.Connection, user_id: int, kind: str) -> bool:
    """True when the *current* policy version was accepted for ``kind``."""

    row = connection.execute(
        "SELECT 1 FROM consents WHERE user_id = ? AND kind = ? AND policy_version = ?",
        (user_id, kind, POLICY_VERSION),
    ).fetchone()
    return row is not None


def consent_status(connection: sqlite3.Connection, user_id: int) -> dict[str, bool]:
    """Current-policy consent state for every known kind."""

    return {kind: has_consent(connection, user_id, kind) for kind in sorted(CONSENT_TEXTS)}


def insert_consent(
    connection: sqlite3.Connection, *, user_id: int, kind: str, granted_at: str
) -> None:
    """Idempotent insert; the caller owns the transaction."""

    connection.execute(
        "INSERT INTO consents (user_id, kind, policy_version, granted_at) VALUES (?, ?, ?, ?) "
        "ON CONFLICT (user_id, kind, policy_version) DO NOTHING",
        (user_id, _validate_kind(kind), POLICY_VERSION, granted_at),
    )


def record_consent(
    connection: sqlite3.Connection, *, user_id: int, kind: str, actor: str | None = None
) -> bool:
    """Record one consent for an existing user. Returns ``False`` if already granted."""

    _validate_kind(kind)
    if not connection.execute("SELECT 1 FROM users WHERE id = ?", (user_id,)).fetchone():
        raise AuthError(f"user {user_id} does not exist.", code="account_missing")
    if has_consent(connection, user_id, kind):
        return False

    with db.transaction(connection):
        insert_consent(connection, user_id=user_id, kind=kind, granted_at=db.utc_now())
        db.record_audit_event(
            connection,
            actor=actor or f"user:{user_id}",
            action="consent.recorded",
            outcome="success",
            target_type="user",
            target_id=str(user_id),
            metadata={"kind": kind, "policy_version": POLICY_VERSION},
        )
    return True
