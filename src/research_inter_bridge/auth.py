"""Accounts, consent, password hashing and authenticated sessions.

Phase 2c of ``task.md`` (§3.1 account creation, consent and privacy, §8 security).
Design rules encoded here:

* Passwords are never stored, logged or audited. Only a salted ``scrypt`` hash is
  persisted, in a self-describing format so parameters can be raised later
  without a schema change.
* Session tokens are returned to the caller exactly once; only their SHA-256 is
  stored, so a database leak does not hand out live sessions.
* Login failures are indistinguishable for "unknown email" and "wrong password",
  and repeated failures lock the account for a fixed window.
* Consent is explicit, versioned and recorded before any document is processed:
  registration refuses to continue without the required consents.
* Deleting an account removes profile rows, documents (files included), sessions
  and consents, and requires the password again.
"""

from __future__ import annotations

import hashlib
import hmac
import math
import re
import secrets
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from . import consent, db, documents, profiles
from .errors import AuthError, ConsentError
from .logging import get_logger

logger = get_logger(__name__)

MIN_PASSWORD_LENGTH = 10
MAX_PASSWORD_LENGTH = 200
MAX_FAILED_ATTEMPTS = 5
LOCKOUT = timedelta(minutes=15)
SESSION_LIFETIME = timedelta(hours=12)

#: Length beats composition rules, so only the worst offenders are refused.
_COMMON_PASSWORDS = frozenset(
    {
        "password",
        "password1",
        "password123",
        "1234567890",
        "qwertyuiop",
        "letmein123",
        "admin12345",
        "iloveyou123",
        "welcome1234",
        "changeme123",
    }
)

_EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[A-Za-z]{2,}$")

#: scrypt work factor. Raising these is safe for existing hashes because the
#: parameters are stored alongside each hash.
SCRYPT_N = 2**14
SCRYPT_R = 8
SCRYPT_P = 1
SCRYPT_DKLEN = 32
MAX_SCRYPT_N = 2**20


@dataclass(frozen=True, slots=True)
class Account:
    """A registered account. Deliberately excludes the password hash."""

    user_id: int
    email: str
    created_at: str


@dataclass(frozen=True, slots=True)
class Session:
    """An authenticated session. ``token`` is only present on creation."""

    user_id: int
    expires_at: str
    token: str | None = None


def _now() -> datetime:
    """Clock hook: tests freeze or shift it instead of sleeping."""

    return datetime.now().astimezone()


def _parse(timestamp: str) -> datetime:
    return datetime.fromisoformat(timestamp)


def normalise_email(value: str) -> str:
    """Lowercase, trim and validate an email address."""

    email = " ".join(str(value).split()).lower()
    if not _EMAIL_RE.match(email):
        raise AuthError(f"email is not a valid address: {value!r}.")
    return email


def validate_password(password: str, *, email: str | None = None) -> str:
    """Check the password policy and return it unchanged.

    Length is the requirement that actually matters; a small blocklist catches the
    most obvious guesses without pretending composition rules add security.
    """

    if not isinstance(password, str) or not password:
        raise AuthError("password is required.")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise AuthError(
            f"password must be at least {MIN_PASSWORD_LENGTH} characters; got {len(password)}."
        )
    if len(password) > MAX_PASSWORD_LENGTH:
        raise AuthError(f"password must be at most {MAX_PASSWORD_LENGTH} characters.")
    if password.casefold() in _COMMON_PASSWORDS:
        raise AuthError("password is one of the most common passwords; choose another one.")
    if email and password.casefold() == normalise_email(email).split("@")[0]:
        raise AuthError("password must not be the same as the email address.")
    if password.strip() != password:
        raise AuthError("password must not start or end with whitespace.")
    return password


def hash_password(password: str, *, salt: bytes | None = None) -> str:
    """Return ``scrypt$n$r$p$salt$hash`` for a password."""

    if len(password) > MAX_PASSWORD_LENGTH:
        raise AuthError(f"password must be at most {MAX_PASSWORD_LENGTH} characters.")
    salt = secrets.token_bytes(16) if salt is None else salt
    derived = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=SCRYPT_N,
        r=SCRYPT_R,
        p=SCRYPT_P,
        dklen=SCRYPT_DKLEN,
    )
    return f"scrypt${SCRYPT_N}${SCRYPT_R}${SCRYPT_P}${salt.hex()}${derived.hex()}"


def verify_password(password: str, stored: str) -> bool:
    """Constant-time verification of a stored hash. Never raises on bad input."""

    try:
        scheme, n_raw, r_raw, p_raw, salt_hex, hash_hex = stored.split("$")
        if scheme != "scrypt":
            return False
        n, r, p = int(n_raw), int(r_raw), int(p_raw)
        if not 2 <= n <= MAX_SCRYPT_N or not 1 <= r <= 32 or not 1 <= p <= 8:
            return False
        expected = bytes.fromhex(hash_hex)
        candidate = hashlib.scrypt(
            password.encode("utf-8"),
            salt=bytes.fromhex(salt_hex),
            n=n,
            r=r,
            p=p,
            dklen=len(expected),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(candidate, expected)


def _account_row(connection: sqlite3.Connection, user_id: int) -> sqlite3.Row:
    row = connection.execute(
        "SELECT user_id, email, password_hash, failed_attempts, locked_until, created_at "
        "FROM accounts WHERE user_id = ?",
        (user_id,),
    ).fetchone()
    if row is None:  # pragma: no cover - only reachable through a torn write
        raise AuthError(f"account {user_id} does not exist.")
    return row


def register(
    connection: sqlite3.Connection,
    *,
    email: str,
    password: str,
    consents: frozenset[str] | set[str] | None = None,
) -> Account:
    """Create a user + account, recording the consents the student accepted.

    ``consents`` must contain :data:`REQUIRED_CONSENTS`; anything unknown is
    rejected so a typo cannot look like consent.
    """

    normalised = normalise_email(email)
    validate_password(password, email=normalised)
    accepted = frozenset(consents or ())
    unknown = accepted - set(consent.CONSENT_TEXTS)
    if unknown:
        raise ConsentError(
            f"unknown consent kind(s): {', '.join(sorted(unknown))}; "
            f"known: {', '.join(sorted(consent.CONSENT_TEXTS))}."
        )
    missing = consent.REQUIRED_CONSENTS - accepted
    if missing:
        raise ConsentError(
            "registration requires explicit consent to: "
            f"{', '.join(sorted(missing))} (see CONSENT_TEXTS)."
        )

    password_hash = hash_password(password)
    now = db.utc_now()
    try:
        with db.transaction(connection):
            cursor = connection.execute("INSERT INTO users (created_at) VALUES (?)", (now,))
            user_id = int(cursor.lastrowid or 0)
            connection.execute(
                "INSERT INTO accounts (user_id, email, password_hash, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (user_id, normalised, password_hash, now, now),
            )
            for kind in sorted(accepted):
                consent.insert_consent(connection, user_id=user_id, kind=kind, granted_at=now)
            db.record_audit_event(
                connection,
                actor=f"user:{user_id}",
                action="account.registered",
                outcome="success",
                target_type="user",
                target_id=str(user_id),
                metadata={"consents": sorted(accepted), "policy_version": consent.POLICY_VERSION},
            )
    except sqlite3.IntegrityError as exc:
        raise AuthError(f"an account with email {normalised!r} already exists.") from exc

    logger.info("account registered user_id=%s", user_id)
    return Account(user_id=user_id, email=normalised, created_at=now)


def authenticate(
    connection: sqlite3.Connection,
    *,
    email: str,
    password: str,
    lifetime: timedelta = SESSION_LIFETIME,
) -> Session:
    """Verify credentials and create a session.

    Unknown email, wrong password and a locked account all raise
    :class:`AuthError`, and the first two share one message so the endpoint cannot
    be used to enumerate registered addresses.
    """

    try:
        normalised = normalise_email(email)
    except AuthError as exc:
        raise AuthError("invalid email or password.") from exc

    row = connection.execute(
        "SELECT user_id, password_hash, failed_attempts, locked_until "
        "FROM accounts WHERE email = ?",
        (normalised,),
    ).fetchone()
    if row is None:
        # Spend comparable time so timing does not reveal that the email is unknown.
        verify_password(password, hash_password("timing-placeholder"))
        _audit_login_failure(connection, reason="unknown_email")
        raise AuthError("invalid email or password.")

    now = _now()
    if row["locked_until"] and _parse(row["locked_until"]) > now:
        remaining = max(1, math.ceil((_parse(row["locked_until"]) - now).total_seconds() / 60))
        _audit_login_failure(connection, reason="locked")
        raise AuthError(f"too many failed attempts; try again in {remaining} minute(s).")

    if not verify_password(password, row["password_hash"]):
        failures = int(row["failed_attempts"]) + 1
        lock_until = (now + LOCKOUT).isoformat() if failures >= MAX_FAILED_ATTEMPTS else None
        with db.transaction(connection):
            connection.execute(
                "UPDATE accounts SET failed_attempts = ?, locked_until = ?, updated_at = ? "
                "WHERE user_id = ?",
                (failures, lock_until, db.utc_now(), row["user_id"]),
            )
            db.record_audit_event(
                connection,
                actor="anonymous",
                action="login.failed",
                outcome="failure",
                target_type="account",
                target_id=str(row["user_id"]),
                metadata={"attempts": failures, "locked": lock_until is not None},
            )
        if lock_until:
            logger.warning("account locked after repeated failures user_id=%s", row["user_id"])
        raise AuthError("invalid email or password.")

    with db.transaction(connection):
        connection.execute(
            "UPDATE accounts SET failed_attempts = 0, locked_until = NULL, updated_at = ? "
            "WHERE user_id = ?",
            (db.utc_now(), row["user_id"]),
        )
    return create_session(connection, user_id=int(row["user_id"]), lifetime=lifetime)


def create_session(
    connection: sqlite3.Connection, *, user_id: int, lifetime: timedelta = SESSION_LIFETIME
) -> Session:
    """Create a session row and return the raw token to the caller exactly once."""

    token = secrets.token_urlsafe(32)
    now = _now()
    expires = now + lifetime
    with db.transaction(connection):
        connection.execute(
            "INSERT INTO sessions (token_hash, user_id, created_at, last_seen_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (hash_token(token), user_id, now.isoformat(), now.isoformat(), expires.isoformat()),
        )
        db.record_audit_event(
            connection,
            actor=f"user:{user_id}",
            action="session.created",
            outcome="success",
            target_type="user",
            target_id=str(user_id),
        )
    return Session(user_id=user_id, expires_at=expires.isoformat(), token=token)


def hash_token(token: str) -> str:
    """Session tokens are stored hashed; the raw value never touches the database."""

    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def resolve_session(connection: sqlite3.Connection, token: str) -> Session:
    """Return the session for a token, or raise :class:`AuthError`.

    Expired and revoked sessions are indistinguishable to the caller. A session's
    ``last_seen_at`` is refreshed at most once a minute so a busy client cannot
    turn every request into a write.
    """

    if not token or len(token) > 200:
        raise AuthError("authentication required.")
    row = connection.execute(
        "SELECT user_id, last_seen_at, expires_at, revoked_at FROM sessions WHERE token_hash = ?",
        (hash_token(token),),
    ).fetchone()
    now = _now()
    if row is None or row["revoked_at"] is not None or _parse(row["expires_at"]) <= now:
        raise AuthError("session is invalid or expired; sign in again.")

    if now - _parse(row["last_seen_at"]) > timedelta(minutes=1):
        connection.execute(
            "UPDATE sessions SET last_seen_at = ? WHERE token_hash = ?",
            (now.isoformat(), hash_token(token)),
        )
    return Session(user_id=int(row["user_id"]), expires_at=row["expires_at"])


def revoke_session(connection: sqlite3.Connection, token: str) -> bool:
    """Revoke one session; returns ``False`` when the token is unknown/expired."""

    row = connection.execute(
        "SELECT user_id FROM sessions WHERE token_hash = ? AND revoked_at IS NULL",
        (hash_token(token),),
    ).fetchone()
    if row is None:
        return False
    with db.transaction(connection):
        connection.execute(
            "UPDATE sessions SET revoked_at = ? WHERE token_hash = ?",
            (db.utc_now(), hash_token(token)),
        )
        db.record_audit_event(
            connection,
            actor=f"user:{row['user_id']}",
            action="session.revoked",
            outcome="success",
            target_type="user",
            target_id=str(row["user_id"]),
        )
    return True


def revoke_all_sessions(connection: sqlite3.Connection, user_id: int) -> int:
    """Revoke every live session for a user (used after a password change)."""

    with db.transaction(connection):
        cursor = connection.execute(
            "UPDATE sessions SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
            (db.utc_now(), user_id),
        )
        revoked = cursor.rowcount
        if revoked:
            db.record_audit_event(
                connection,
                actor=f"user:{user_id}",
                action="session.revoked_all",
                outcome="success",
                target_type="user",
                target_id=str(user_id),
                metadata={"sessions": revoked},
            )
    return revoked


def change_password(
    connection: sqlite3.Connection,
    *,
    user_id: int,
    current_password: str,
    new_password: str,
) -> None:
    """Change a password after re-checking the old one, revoking other sessions."""

    row = _account_row(connection, user_id)
    if not verify_password(current_password, row["password_hash"]):
        raise AuthError("current password is incorrect.")
    validate_password(new_password, email=row["email"])
    if verify_password(new_password, row["password_hash"]):
        raise AuthError("the new password must differ from the current one.")

    new_hash = hash_password(new_password)
    with db.transaction(connection):
        connection.execute(
            "UPDATE accounts SET password_hash = ?, updated_at = ? WHERE user_id = ?",
            (new_hash, db.utc_now(), user_id),
        )
        db.record_audit_event(
            connection,
            actor=f"user:{user_id}",
            action="password.changed",
            outcome="success",
            target_type="user",
            target_id=str(user_id),
        )
    revoke_all_sessions(connection, user_id)


def delete_account(
    connection: sqlite3.Connection,
    *,
    user_id: int,
    password: str,
    data_directory: Path | str,
) -> None:
    """Delete an account and all of its data after re-checking the password."""

    row = _account_row(connection, user_id)
    if not verify_password(password, row["password_hash"]):
        raise AuthError("password is incorrect; account was not deleted.")

    documents.purge_user_documents(
        connection, user_id=user_id, data_directory=data_directory, actor=f"user:{user_id}"
    )
    profiles.delete_user(connection, user_id, actor=f"user:{user_id}")


def export_account_data(connection: sqlite3.Connection, user_id: int) -> dict[str, Any]:
    """Return the student's own data as a JSON-serialisable structure.

    Password hashes and session tokens are excluded by construction; document
    files themselves are not copied, only their metadata.
    """

    row = connection.execute(
        "SELECT email, created_at FROM accounts WHERE user_id = ?", (user_id,)
    ).fetchone()
    if row is None:
        raise AuthError(f"account {user_id} does not exist.")

    try:
        profile = profiles.load_profile(connection, user_id)
        profile_payload: dict[str, Any] | None = {
            field: getattr(profile, field)
            for field in (
                "full_name",
                "preferred_name",
                "contact_email",
                "phone",
                "institution",
                "department",
                "country",
                "degree_type",
                "current_year",
                "expected_graduation",
                "gpa",
                "gpa_scale",
                "coursework",
                "skills",
                "research_keywords",
                "target_countries",
                "internship_start",
                "internship_end",
                "funding_required",
                "consent_given_at",
            )
        }
    except profiles.ProfileNotFoundError:
        profile_payload = None

    return {
        "account": {"user_id": user_id, "email": row["email"], "created_at": row["created_at"]},
        "consents": consent.consent_status(connection, user_id),
        "profile": profile_payload,
        "documents": [
            {
                "id": record.id,
                "kind": record.kind,
                "display_name": record.display_name,
                "media_type": record.media_type,
                "size_bytes": record.size_bytes,
                "sha256": record.sha256,
                "status": record.status,
                "uploaded_at": record.uploaded_at,
            }
            for record in documents.list_documents(connection, user_id)
        ],
        "facts": [
            {
                "field": fact["field"],
                "value": fact["value"],
                "status": fact["status"],
                "source": fact["source"],
            }
            for fact in profiles.list_profile_facts(connection, user_id)
        ],
    }


def _audit_login_failure(connection: sqlite3.Connection, *, reason: str) -> None:
    """Record a failed login attempt without storing the address or password."""

    db.record_audit_event(
        connection,
        actor="anonymous",
        action="login.failed",
        outcome="failure",
        target_type="account",
        metadata={"reason": reason},
    )
