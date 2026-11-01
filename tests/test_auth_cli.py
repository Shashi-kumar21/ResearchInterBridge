"""CLI surface for accounts, consent and the local session file."""

from __future__ import annotations

import io
import json
import os
import sqlite3
import sys
from pathlib import Path

import pytest

from research_inter_bridge import auth, consent

EMAIL = "asha@example.edu"
PASSWORD = "correct horse battery"
NEW_PASSWORD = "an even better passphrase"


@pytest.fixture
def stdin(monkeypatch: pytest.MonkeyPatch):
    """Feed lines to ``--password-stdin`` without touching the real terminal."""

    def feed(*lines: str) -> None:
        monkeypatch.setattr(sys, "stdin", io.StringIO("\n".join(lines) + "\n"))

    return feed


def _register(cli, stdin, *extra: str) -> int:
    stdin(PASSWORD)
    code, _out = cli.run("register", "--email", EMAIL, "--password-stdin", *extra)
    return code


def test_register_then_login_whoami_logout(cli, stdin) -> None:
    stdin(PASSWORD)
    code, out = cli.run("register", "--email", EMAIL, "--password-stdin")
    assert code == 0
    assert json.loads(out)["email"] == EMAIL

    stdin(PASSWORD)
    code, out = cli.run("login", "--email", EMAIL, "--password-stdin")
    assert code == 0
    user_id = json.loads(out)["user_id"]

    session_file = Path(cli.settings.data_directory) / "session"
    assert session_file.is_file()

    code, out = cli.run("whoami")
    assert code == 0
    assert json.loads(out)["user_id"] == user_id

    code, out = cli.run("logout")
    assert code == 0
    assert json.loads(out) == {"signed_out": True, "revoked": True}
    assert not session_file.exists()

    code, _out = cli.run("whoami")
    assert code == 1


def test_register_requires_consent_when_narrowed(cli, stdin) -> None:
    stdin(PASSWORD)
    code, out = cli.run(
        "register",
        "--email",
        EMAIL,
        "--password-stdin",
        "--consent",
        "personal_data",
    )

    assert code == 1
    assert out == ""


def test_register_rejects_weak_password(cli, stdin) -> None:
    stdin("short")
    code, out = cli.run("register", "--email", EMAIL, "--password-stdin")

    assert code == 1
    assert out == ""


def test_register_rejects_duplicate_email(cli, stdin) -> None:
    assert _register(cli, stdin) == 0
    assert _register(cli, stdin) == 1


def test_login_with_wrong_password_fails_and_writes_no_session(cli, stdin) -> None:
    assert _register(cli, stdin) == 0

    stdin("wrong password here")
    code, _out = cli.run("login", "--email", EMAIL, "--password-stdin")

    assert code == 1
    assert not (Path(cli.settings.data_directory) / "session").exists()


def test_logout_without_session_is_harmless(cli) -> None:
    code, out = cli.run("logout")

    assert code == 0
    assert json.loads(out) == {"signed_out": True, "revoked": False}


def test_consent_status_lists_every_kind(cli, stdin) -> None:
    stdin(PASSWORD)
    _code, out = cli.run("register", "--email", EMAIL, "--password-stdin")
    user_id = json.loads(out)["user_id"]

    code, out = cli.run("consent-status", "--user-id", str(user_id))
    assert code == 0
    assert json.loads(out) == {
        "document_processing": True,
        "gmail_access": False,
        "personal_data": True,
    }

    code, out = cli.run("consent-accept", "--user-id", str(user_id), "--kind", "gmail_access")
    assert code == 0
    assert json.loads(out) == {"granted": True, "kind": "gmail_access"}

    code, out = cli.run("consent-accept", "--user-id", str(user_id), "--kind", "gmail_access")
    assert code == 0
    assert json.loads(out) == {"granted": False, "kind": "gmail_access"}


def test_consent_for_unknown_user_fails(cli) -> None:
    code, _out = cli.run("consent-accept", "--user-id", "42", "--kind", "personal_data")

    assert code == 1


def test_session_file_holds_the_token_not_the_password(cli, stdin) -> None:
    assert _register(cli, stdin) == 0
    stdin(PASSWORD)
    code, _out = cli.run("login", "--email", EMAIL, "--password-stdin")
    assert code == 0

    token = (Path(cli.settings.data_directory) / "session").read_text(encoding="utf-8").strip()
    assert token
    assert PASSWORD not in token

    with sqlite3.connect(cli.settings.database_path) as connection:
        stored = [row[0] for row in connection.execute("SELECT token_hash FROM sessions")]
    assert stored == [auth.hash_token(token)]


def test_session_file_permissions_are_owner_only(cli, stdin) -> None:
    if os.name != "posix":
        pytest.skip("file modes are a POSIX concept; the Linux CI job covers this")

    assert _register(cli, stdin) == 0
    stdin(PASSWORD)
    assert cli.run("login", "--email", EMAIL, "--password-stdin")[0] == 0

    mode = (Path(cli.settings.data_directory) / "session").stat().st_mode & 0o777
    assert mode == 0o600


def test_register_shows_the_consent_texts_it_records(cli, stdin) -> None:
    stdin(PASSWORD)
    code, out = cli.run("register", "--email", EMAIL, "--password-stdin")
    assert code == 0

    payload = json.loads(out)
    assert payload["consents"] == ["document_processing", "personal_data"]
    assert payload["policy_version"] == consent.POLICY_VERSION

    # The student must see what they are granting, not just a flag name.
    assert f"policy version {consent.POLICY_VERSION}" in cli.last_err
    assert consent.CONSENT_TEXTS["personal_data"] in cli.last_err
    assert consent.CONSENT_TEXTS["document_processing"] in cli.last_err
    assert consent.CONSENT_TEXTS["gmail_access"] not in cli.last_err


def test_register_shows_only_the_narrowed_consent(cli, stdin) -> None:
    stdin(PASSWORD)
    code, _out = cli.run(
        "register", "--email", EMAIL, "--password-stdin", "--consent", "personal_data"
    )

    assert code == 1
    assert consent.CONSENT_TEXTS["personal_data"] in cli.last_err
    assert consent.CONSENT_TEXTS["document_processing"] not in cli.last_err
