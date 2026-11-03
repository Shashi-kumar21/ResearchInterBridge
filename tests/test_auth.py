"""Phase 2c — accounts, password hashing, consent, sessions and privacy.

These tests deliberately look at the stored rows: a passing API is not enough,
the database must not contain a password, a session token or an email address in
a failed-login audit row.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from research_inter_bridge import auth, consent, db, documents, profiles
from research_inter_bridge.auth import (
    LOCKOUT,
    MAX_FAILED_ATTEMPTS,
    MIN_PASSWORD_LENGTH,
    SESSION_LIFETIME,
    Account,
    authenticate,
    change_password,
    delete_account,
    export_account_data,
    get_account,
    hash_password,
    hash_token,
    register,
    resolve_session,
    revoke_all_sessions,
    revoke_session,
    validate_password,
    verify_password,
)
from research_inter_bridge.errors import AuthError, ConsentError
from research_inter_bridge.profiles import StudentProfile

EMAIL = "asha@example.edu"
PASSWORD = "correct horse battery"
NEW_PASSWORD = "an even better passphrase"

ALL_CONSENTS = frozenset(consent.REQUIRED_CONSENTS)


@pytest.fixture
def connection(tmp_path: Path):
    with db.session(tmp_path / "app.db") as conn:
        db.apply_migrations(conn)
        yield conn


@pytest.fixture
def data_dir(tmp_path: Path) -> Path:
    return tmp_path / "data"


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> SimpleNamespace:
    """Freeze ``auth._now`` so lockout and expiry tests never sleep."""

    state = {"now": datetime(2026, 11, 1, 9, 0, tzinfo=UTC)}

    def fake_now() -> datetime:
        return state["now"]

    def advance(**delta: int) -> datetime:
        state["now"] = state["now"] + timedelta(**delta)
        return state["now"]

    monkeypatch.setattr(auth, "_now", fake_now)
    return SimpleNamespace(advance=advance, get=lambda: state["now"])


@pytest.fixture
def account(connection: sqlite3.Connection) -> Account:
    return register(connection, email=EMAIL, password=PASSWORD, consents=ALL_CONSENTS)


# --- registration -----------------------------------------------------------


def test_register_creates_user_account_and_consents(
    connection: sqlite3.Connection, account: Account
) -> None:
    assert account.email == EMAIL
    assert account.user_id > 0

    row = connection.execute(
        "SELECT email, password_hash FROM accounts WHERE user_id = ?", (account.user_id,)
    ).fetchone()
    assert row["email"] == EMAIL
    assert row["password_hash"].startswith("scrypt$")

    assert consent.consent_status(connection, account.user_id) == {
        "document_processing": True,
        "gmail_access": False,
        "personal_data": True,
    }


def test_register_normalises_email(connection: sqlite3.Connection) -> None:
    account = register(
        connection,
        email="  Asha.Verma@Example.EDU ",
        password=PASSWORD,
        consents=ALL_CONSENTS,
    )
    assert account.email == "asha.verma@example.edu"


@pytest.mark.parametrize(
    "missing",
    [
        frozenset(),
        frozenset({"personal_data"}),
        frozenset({"document_processing"}),
    ],
)
def test_register_requires_each_required_consent(
    connection: sqlite3.Connection, missing: frozenset[str]
) -> None:
    with pytest.raises(ConsentError, match="requires explicit consent"):
        register(connection, email=EMAIL, password=PASSWORD, consents=missing)

    assert connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0


def test_register_rejects_unknown_consent_kind(connection: sqlite3.Connection) -> None:
    with pytest.raises(ConsentError, match="unknown consent kind"):
        register(
            connection,
            email=EMAIL,
            password=PASSWORD,
            consents=ALL_CONSENTS | {"marketing"},
        )


def test_register_rejects_duplicate_email_in_any_case(
    connection: sqlite3.Connection, account: Account
) -> None:
    with pytest.raises(AuthError, match="already exists"):
        register(connection, email="ASHA@example.edu", password=PASSWORD, consents=ALL_CONSENTS)


@pytest.mark.parametrize(
    "bad_email",
    ["", "asha", "asha@", "@example.edu", "asha@example", "a b@example.edu", "x@y.z"],
)
def test_register_rejects_invalid_email(connection: sqlite3.Connection, bad_email: str) -> None:
    with pytest.raises(AuthError):
        register(connection, email=bad_email, password=PASSWORD, consents=ALL_CONSENTS)


def test_register_does_not_store_the_password(
    connection: sqlite3.Connection, account: Account
) -> None:
    dumped = "\n".join(connection.iterdump())

    assert PASSWORD not in dumped
    assert PASSWORD.encode("utf-8").hex() not in dumped


# --- password policy and hashing -------------------------------------------


@pytest.mark.parametrize(
    ("password", "match"),
    [
        ("", "required"),
        ("short", "at least"),
        ("x" * (auth.MAX_PASSWORD_LENGTH + 1), "at most"),
        ("password123", "common password"),
        ("  padded passphrase  ", "whitespace"),
    ],
)
def test_validate_password_rejections(password: str, match: str) -> None:
    with pytest.raises(AuthError, match=match):
        validate_password(password)


def test_validate_password_rejects_email_local_part() -> None:
    with pytest.raises(AuthError, match="same as the email"):
        validate_password("asha.verma", email="asha.verma@example.edu")


def test_validate_password_accepts_a_long_passphrase() -> None:
    assert validate_password(PASSWORD) == PASSWORD
    assert len(PASSWORD) >= MIN_PASSWORD_LENGTH


def test_hash_format_and_verification() -> None:
    stored = hash_password(PASSWORD)

    scheme, n, r, p, salt, digest = stored.split("$")
    assert scheme == "scrypt"
    assert (n, r, p) == (str(auth.SCRYPT_N), str(auth.SCRYPT_R), str(auth.SCRYPT_P))
    assert len(bytes.fromhex(salt)) == 16
    assert len(bytes.fromhex(digest)) == auth.SCRYPT_DKLEN

    assert verify_password(PASSWORD, stored) is True
    assert verify_password(PASSWORD + "x", stored) is False


def test_hashes_use_a_fresh_salt() -> None:
    first = hash_password(PASSWORD)
    second = hash_password(PASSWORD)

    assert first != second
    assert verify_password(PASSWORD, first)
    assert verify_password(PASSWORD, second)


@pytest.mark.parametrize(
    "stored",
    [
        "",
        "not-a-hash",
        "bcrypt$16384$8$1$aa$bb",
        "scrypt$16384$8$1$zz$bb",
        "scrypt$1$8$1$00112233445566778899aabbccddeeff$00",
        "scrypt$99999999$8$1$00112233445566778899aabbccddeeff$00",
    ],
)
def test_verify_password_survives_corrupt_hashes(stored: str) -> None:
    assert verify_password(PASSWORD, stored) is False


# --- login ------------------------------------------------------------------


def test_authenticate_returns_a_token_and_stores_only_its_hash(
    connection: sqlite3.Connection, account: Account
) -> None:
    session = authenticate(connection, email=EMAIL, password=PASSWORD)

    assert session.user_id == account.user_id
    assert session.token
    assert session.expires_at

    rows = connection.execute("SELECT token_hash FROM sessions").fetchall()
    assert [row["token_hash"] for row in rows] == [hash_token(session.token)]
    assert session.token not in "\n".join(connection.iterdump())


def test_authenticate_accepts_differently_cased_email(
    connection: sqlite3.Connection, account: Account
) -> None:
    assert authenticate(connection, email="ASHA@Example.edu", password=PASSWORD).user_id == (
        account.user_id
    )


def test_unknown_email_and_wrong_password_are_indistinguishable(
    connection: sqlite3.Connection, account: Account
) -> None:
    with pytest.raises(AuthError) as unknown:
        authenticate(connection, email="nobody@example.edu", password=PASSWORD)
    first = str(unknown.value)

    with pytest.raises(AuthError) as wrong:
        authenticate(connection, email=EMAIL, password="wrong password here")
    second = str(wrong.value)

    assert first == second == "invalid email or password."


def test_malformed_email_does_not_leak_validation_details(
    connection: sqlite3.Connection,
) -> None:
    with pytest.raises(AuthError, match="invalid email or password"):
        authenticate(connection, email="not-an-email", password=PASSWORD)


def test_login_failure_audit_has_no_email_or_password(
    connection: sqlite3.Connection, account: Account
) -> None:
    with pytest.raises(AuthError):
        authenticate(connection, email=EMAIL, password="wrong password here")

    row = connection.execute(
        "SELECT actor, metadata_json FROM audit_events WHERE action = 'login.failed'"
    ).fetchone()
    metadata = json.loads(row["metadata_json"])
    assert row["actor"] == "anonymous"
    assert metadata == {"attempts": 1, "locked": False}
    assert EMAIL not in row["metadata_json"]
    assert "wrong password here" not in row["metadata_json"]


def test_failures_lock_the_account_and_success_resets_the_counter(
    connection: sqlite3.Connection, account: Account, clock: SimpleNamespace
) -> None:
    for expected in range(1, MAX_FAILED_ATTEMPTS):
        with pytest.raises(AuthError, match="invalid email or password"):
            authenticate(connection, email=EMAIL, password="wrong password here")
        attempts = connection.execute(
            "SELECT failed_attempts FROM accounts WHERE user_id = ?", (account.user_id,)
        ).fetchone()["failed_attempts"]
        assert attempts == expected

    with pytest.raises(AuthError, match="invalid email or password"):
        authenticate(connection, email=EMAIL, password="wrong password here")

    with pytest.raises(AuthError, match="too many failed attempts"):
        authenticate(connection, email=EMAIL, password=PASSWORD)

    clock.advance(minutes=LOCKOUT.seconds // 60 + 1)
    session = authenticate(connection, email=EMAIL, password=PASSWORD)
    assert session.token

    state = connection.execute(
        "SELECT failed_attempts, locked_until FROM accounts WHERE user_id = ?",
        (account.user_id,),
    ).fetchone()
    assert state["failed_attempts"] == 0
    assert state["locked_until"] is None


def test_lock_message_counts_down_minutes(
    connection: sqlite3.Connection, account: Account, clock: SimpleNamespace
) -> None:
    for _ in range(MAX_FAILED_ATTEMPTS):
        with pytest.raises(AuthError):
            authenticate(connection, email=EMAIL, password="wrong password here")

    with pytest.raises(AuthError, match=r"try again in 15 minute\(s\)"):
        authenticate(connection, email=EMAIL, password=PASSWORD)


# --- sessions ---------------------------------------------------------------


def test_session_round_trip(connection: sqlite3.Connection, account: Account) -> None:
    token = auth.create_session(connection, user_id=account.user_id).token

    session = resolve_session(connection, token or "")
    assert session.user_id == account.user_id
    assert session.token is None


@pytest.mark.parametrize("token", ["", "x" * 201, "not-a-real-token"])
def test_resolve_rejects_unknown_tokens(connection: sqlite3.Connection, token: str) -> None:
    with pytest.raises(AuthError):
        resolve_session(connection, token)


def test_session_expires(connection: sqlite3.Connection, account: Account, clock) -> None:
    token = auth.create_session(
        connection, user_id=account.user_id, lifetime=timedelta(minutes=5)
    ).token

    clock.advance(minutes=4)
    assert resolve_session(connection, token or "").user_id == account.user_id

    clock.advance(minutes=2)
    with pytest.raises(AuthError, match="invalid or expired"):
        resolve_session(connection, token or "")


def test_default_session_lifetime_is_short(
    connection: sqlite3.Connection, account: Account, clock
) -> None:
    token = auth.create_session(connection, user_id=account.user_id).token

    clock.advance(hours=SESSION_LIFETIME.seconds // 3600 - 1)
    assert resolve_session(connection, token or "")

    clock.advance(hours=2)
    with pytest.raises(AuthError):
        resolve_session(connection, token or "")


def test_resolve_refreshes_last_seen_at_at_most_once_a_minute(
    connection: sqlite3.Connection, account: Account, clock
) -> None:
    token = auth.create_session(connection, user_id=account.user_id).token
    first_seen = connection.execute("SELECT last_seen_at FROM sessions").fetchone()["last_seen_at"]

    resolve_session(connection, token or "")
    assert (
        connection.execute("SELECT last_seen_at FROM sessions").fetchone()["last_seen_at"]
        == first_seen
    )

    clock.advance(minutes=2)
    resolve_session(connection, token or "")
    refreshed = connection.execute("SELECT last_seen_at FROM sessions").fetchone()["last_seen_at"]
    assert refreshed != first_seen


def test_revoke_session(connection: sqlite3.Connection, account: Account) -> None:
    token = auth.create_session(connection, user_id=account.user_id).token or ""

    assert revoke_session(connection, token) is True
    assert revoke_session(connection, token) is False
    assert revoke_session(connection, "unknown-token") is False

    with pytest.raises(AuthError, match="invalid or expired"):
        resolve_session(connection, token)


def test_revoke_all_sessions(connection: sqlite3.Connection, account: Account) -> None:
    tokens = [auth.create_session(connection, user_id=account.user_id).token for _ in range(3)]

    assert revoke_all_sessions(connection, account.user_id) == 3
    assert revoke_all_sessions(connection, account.user_id) == 0
    for token in tokens:
        with pytest.raises(AuthError):
            resolve_session(connection, token or "")


# --- password change --------------------------------------------------------


def test_change_password_requires_the_current_password(
    connection: sqlite3.Connection, account: Account
) -> None:
    with pytest.raises(AuthError, match="current password is incorrect"):
        change_password(
            connection,
            user_id=account.user_id,
            current_password="wrong password here",
            new_password=NEW_PASSWORD,
        )

    assert authenticate(connection, email=EMAIL, password=PASSWORD).token


@pytest.mark.parametrize(
    ("new_password", "match"),
    [
        (PASSWORD, "must differ"),
        ("short", "at least"),
        ("password123", "common password"),
    ],
)
def test_change_password_validates_the_new_password(
    connection: sqlite3.Connection, account: Account, new_password: str, match: str
) -> None:
    with pytest.raises(AuthError, match=match):
        change_password(
            connection,
            user_id=account.user_id,
            current_password=PASSWORD,
            new_password=new_password,
        )


def test_change_password_revokes_existing_sessions(
    connection: sqlite3.Connection, account: Account
) -> None:
    token = auth.create_session(connection, user_id=account.user_id).token or ""

    change_password(
        connection,
        user_id=account.user_id,
        current_password=PASSWORD,
        new_password=NEW_PASSWORD,
    )

    with pytest.raises(AuthError):
        resolve_session(connection, token)
    with pytest.raises(AuthError):
        authenticate(connection, email=EMAIL, password=PASSWORD)
    assert authenticate(connection, email=EMAIL, password=NEW_PASSWORD).token


# --- deletion and export ----------------------------------------------------


def _with_data(
    connection: sqlite3.Connection, account: Account, data_dir: Path
) -> documents.DocumentRecord:
    consent.record_consent(connection, user_id=account.user_id, kind="document_processing")
    record = documents.save_document(
        connection,
        user_id=account.user_id,
        display_name="cv.txt",
        payload=b"Full name: Asha Verma\nSkills: Python\n",
        data_directory=data_dir,
        actor=f"user:{account.user_id}",
    )
    profiles.save_profile(
        connection,
        StudentProfile(
            user_id=account.user_id,
            full_name="Asha Verma",
            contact_email=EMAIL,
        ),
        actor=f"user:{account.user_id}",
    )
    return record


def test_delete_account_needs_the_password(
    connection: sqlite3.Connection, account: Account, data_dir: Path
) -> None:
    _with_data(connection, account, data_dir)

    with pytest.raises(AuthError, match="password is incorrect"):
        delete_account(
            connection, user_id=account.user_id, password="nope nope nope", data_directory=data_dir
        )

    assert connection.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 1
    assert profiles.load_profile(connection, account.user_id).full_name == "Asha Verma"


def test_delete_account_removes_rows_and_files(
    connection: sqlite3.Connection, account: Account, data_dir: Path
) -> None:
    record = _with_data(connection, account, data_dir)
    stored_file = data_dir / "documents" / record.storage_path
    assert stored_file.is_file()

    delete_account(connection, user_id=account.user_id, password=PASSWORD, data_directory=data_dir)

    assert not stored_file.exists()
    for table in ("users", "accounts", "consents", "sessions", "student_profiles"):
        assert connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0  # noqa: S608
    assert documents.list_documents(connection, account.user_id) == ()


def test_export_includes_own_data_but_no_secrets(
    connection: sqlite3.Connection, account: Account, data_dir: Path
) -> None:
    _with_data(connection, account, data_dir)
    token = auth.create_session(connection, user_id=account.user_id).token or ""

    exported = export_account_data(connection, account.user_id)
    serialised = json.dumps(exported)

    assert exported["account"]["email"] == EMAIL
    assert exported["profile"]["full_name"] == "Asha Verma"
    assert exported["consents"]["personal_data"] is True
    assert [doc["display_name"] for doc in exported["documents"]] == ["cv.txt"]
    assert exported["facts"] == []

    assert PASSWORD not in serialised
    assert token not in serialised
    assert "password_hash" not in serialised
    assert "token_hash" not in serialised


def test_export_without_profile_or_documents(
    connection: sqlite3.Connection, account: Account
) -> None:
    exported = export_account_data(connection, account.user_id)

    assert exported["profile"] is None
    assert exported["documents"] == []


def test_export_for_unknown_account_fails(connection: sqlite3.Connection) -> None:
    with pytest.raises(AuthError, match="does not exist"):
        export_account_data(connection, 999)


def test_registration_audit_records_consent_version_without_secrets(
    connection: sqlite3.Connection, account: Account
) -> None:
    row = connection.execute(
        "SELECT metadata_json FROM audit_events WHERE action = 'account.registered'"
    ).fetchone()
    metadata = json.loads(row["metadata_json"])

    assert metadata["consents"] == sorted(ALL_CONSENTS)
    assert metadata["policy_version"] == consent.POLICY_VERSION
    assert PASSWORD not in row["metadata_json"]


def test_lockout_window_resets_the_attempt_budget(
    connection: sqlite3.Connection, account: Account, clock: SimpleNamespace
) -> None:
    """A single typo after the window must not re-lock the account.

    Regression for QA finding 1: carrying ``failed_attempts`` past an elapsed
    window turned the lockout into a cheap permanent DoS for a known address.
    """

    for _ in range(MAX_FAILED_ATTEMPTS):
        with pytest.raises(AuthError):
            authenticate(connection, email=EMAIL, password="wrong password here")

    clock.advance(minutes=LOCKOUT.seconds // 60 + 1)
    with pytest.raises(AuthError, match="invalid email or password"):
        authenticate(connection, email=EMAIL, password="wrong password here")

    state = connection.execute(
        "SELECT failed_attempts, locked_until FROM accounts WHERE user_id = ?",
        (account.user_id,),
    ).fetchone()
    assert state["failed_attempts"] == 1
    assert state["locked_until"] is None

    assert authenticate(connection, email=EMAIL, password=PASSWORD).user_id == account.user_id


def test_unknown_email_and_wrong_password_do_equal_hashing_work(
    connection: sqlite3.Connection, account: Account, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Both failure paths must run exactly one password hash comparison.

    Regression for QA finding 2: hashing a fresh placeholder per request made an
    unknown email about twice as slow as a wrong password, inverting the oracle.
    """

    calls: list[str] = []
    real_verify = auth.verify_password

    def counting_verify(password: str, stored: str) -> bool:
        calls.append(stored)
        return real_verify(password, stored)

    monkeypatch.setattr(auth, "verify_password", counting_verify)

    with pytest.raises(AuthError):
        authenticate(connection, email="nobody@example.edu", password=PASSWORD)
    unknown_calls = len(calls)

    calls.clear()
    with pytest.raises(AuthError):
        authenticate(connection, email=EMAIL, password="wrong password here")

    assert unknown_calls == 1
    assert len(calls) == 1
    # The placeholder hash is cached, so repeated unknown-email probes never
    # pay for a second scrypt call.
    monkeypatch.setattr(auth, "hash_password", _explode)
    with pytest.raises(AuthError, match="invalid email or password"):
        authenticate(connection, email="nobody@example.edu", password=PASSWORD)


def _explode(*_args: object, **_kwargs: object) -> str:
    raise AssertionError("hash_password must not run per request")


def test_change_password_clears_a_lockout(connection: sqlite3.Connection, account: Account) -> None:
    for _ in range(MAX_FAILED_ATTEMPTS):
        with pytest.raises(AuthError):
            authenticate(connection, email=EMAIL, password="wrong password here")

    change_password(
        connection,
        user_id=account.user_id,
        current_password=PASSWORD,
        new_password=NEW_PASSWORD,
    )

    assert authenticate(connection, email=EMAIL, password=NEW_PASSWORD).token


def test_get_account_returns_only_public_fields(
    connection: sqlite3.Connection, account: Account
) -> None:
    record = get_account(connection, account.user_id)

    assert record == account
    assert record.email == EMAIL
    assert not hasattr(record, "password_hash")


def test_get_account_rejects_an_unknown_user(connection: sqlite3.Connection) -> None:
    with pytest.raises(AuthError) as excinfo:
        get_account(connection, account_id := 424242)

    assert excinfo.value.code == "account_missing"
    assert str(account_id) in str(excinfo.value)
