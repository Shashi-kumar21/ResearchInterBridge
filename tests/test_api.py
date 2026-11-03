"""HTTP API tests: auth, consent, CORS, error shape and cookie handling.

Everything runs against a temporary database through ``fastapi.testclient``;
no test binds a port, opens a browser or touches the network.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from httpx import Response

from research_inter_bridge import auth, db, ratelimit
from research_inter_bridge import documents as document_service
from research_inter_bridge.api import create_app
from research_inter_bridge.config import Settings

EMAIL = "student@example.edu"
OTHER_EMAIL = "second@example.edu"
PASSWORD = "correct horse battery staple"
CONSENTS = ["personal_data", "document_processing"]
ORIGIN = "http://localhost:3000"


def make_settings(tmp_path: Path, **overrides: object) -> Settings:
    defaults: dict[str, object] = {
        "database_path": tmp_path / "api.db",
        "data_directory": tmp_path / "data",
        "cors_origins": (ORIGIN,),
    }
    defaults.update(overrides)
    return Settings(**defaults)  # type: ignore[arg-type]


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return make_settings(tmp_path)


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    # The context manager runs the lifespan, which applies migrations exactly as
    # `serve` does at startup.
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def register(
    client: TestClient,
    *,
    email: str = EMAIL,
    password: str = PASSWORD,
    consents: list[str] | None = None,
) -> Response:
    return client.post(
        "/api/auth/register",
        json={
            "email": email,
            "password": password,
            "consents": CONSENTS if consents is None else consents,
        },
    )


def error_code(response: Response) -> str:
    return str(response.json()["error"]["code"])


# --------------------------------------------------------------------------- #
# system + public catalogue
# --------------------------------------------------------------------------- #


def test_health_reports_schema_without_leaking_paths(client: TestClient) -> None:
    response = client.get("/api/health")

    assert response.status_code == 200
    body = response.json()
    assert body["ok"] is True
    assert body["schema_version"] == db.latest_version()
    assert body["expected_schema_version"] == db.latest_version()
    assert body["checks"] == {"database_reachable": True, "schema_current": True}
    # The probe is unauthenticated: it must not hand out an operator's filesystem.
    assert set(body) == {"ok", "schema_version", "expected_schema_version", "checks"}
    assert "/" not in response.text


def test_health_is_503_before_migrations_have_run(tmp_path: Path) -> None:
    app = create_app(make_settings(tmp_path))

    # No `with` block: the lifespan is skipped, so the file does not exist yet.
    response = TestClient(app).get("/api/health")

    assert response.status_code == 503
    assert response.json()["ok"] is False
    assert response.json()["checks"]["database_reachable"] is False


def test_consent_catalogue_is_public_and_matches_the_service(client: TestClient) -> None:
    response = client.get("/api/consents/catalogue")

    assert response.status_code == 200
    body = response.json()
    assert body["policy_version"] == "2026-10-31"
    kinds = {item["kind"]: item for item in body["consents"]}
    assert set(kinds) == {"personal_data", "document_processing", "gmail_access"}
    assert kinds["personal_data"]["required_for_registration"] is True
    assert kinds["document_processing"]["required_for_registration"] is True
    assert kinds["gmail_access"]["required_for_registration"] is False
    assert all(item["text"] for item in body["consents"])


# --------------------------------------------------------------------------- #
# registration
# --------------------------------------------------------------------------- #


def test_register_creates_account_sets_cookie_and_signs_in(client: TestClient) -> None:
    response = register(client)

    assert response.status_code == 201
    body = response.json()
    assert body["account"]["email"] == EMAIL
    assert body["account"]["user_id"] >= 1
    assert body["expires_at"]

    cookie = response.headers["set-cookie"]
    assert cookie.startswith("rib_session=")
    assert "HttpOnly" in cookie
    assert "SameSite=lax" in cookie
    assert "Path=/" in cookie
    assert "Secure" not in cookie  # local HTTP development

    me = client.get("/api/auth/me")
    assert me.status_code == 200
    assert me.json()["account"]["email"] == EMAIL


def test_register_stores_no_secret_and_only_hashed_session_tokens(
    client: TestClient, settings: Settings
) -> None:
    response = register(client)
    token = client.cookies.get("rib_session")

    assert token
    assert PASSWORD not in response.text
    assert "scrypt" not in response.text

    with db.session(settings.database_path) as connection:
        stored = connection.execute("SELECT token_hash FROM sessions").fetchall()
        hashes = {row["token_hash"] for row in stored}
        account = connection.execute("SELECT password_hash FROM accounts").fetchone()

    assert auth.hash_token(token) in hashes
    assert token not in hashes
    assert account["password_hash"].startswith("scrypt$")
    assert PASSWORD not in account["password_hash"]


def test_register_refuses_a_duplicate_email(client: TestClient) -> None:
    assert register(client).status_code == 201

    response = register(client, email=EMAIL.upper())

    assert response.status_code == 409
    assert error_code(response) == "email_taken"


@pytest.mark.parametrize(
    ("consents", "status_code", "code"),
    [
        ([], 403, "consent_required"),
        (["personal_data"], 403, "consent_required"),
        (["personal_data", "document_processing", "newsletter"], 400, "unknown_consent"),
    ],
)
def test_register_requires_exactly_the_known_required_consents(
    client: TestClient, consents: list[str], status_code: int, code: str
) -> None:
    response = register(client, consents=consents)

    assert response.status_code == status_code
    assert error_code(response) == code
    assert "Set-Cookie" not in response.headers


@pytest.mark.parametrize(
    ("email", "password"),
    [
        (EMAIL, "short"),
        ("studentname@example.edu", "studentname"),  # the email's local part
        (EMAIL, " leading-space-password"),
        (EMAIL, "password123"),
    ],
)
def test_register_applies_the_password_policy(
    client: TestClient, email: str, password: str
) -> None:
    response = register(client, email=email, password=password)

    assert response.status_code == 400
    assert error_code(response) == "invalid_input"
    assert password not in response.text


def test_register_rejects_an_invalid_email_without_echoing_it(client: TestClient) -> None:
    response = register(client, email="not-an-email")

    assert response.status_code == 400
    assert error_code(response) == "invalid_input"
    assert "not-an-email" in response.text  # safe: it is not a credential


# --------------------------------------------------------------------------- #
# login, sessions, logout
# --------------------------------------------------------------------------- #


def test_login_sets_a_fresh_cookie(client: TestClient) -> None:
    assert register(client).status_code == 201
    client.cookies.clear()

    response = client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})

    assert response.status_code == 200
    assert response.json()["account"]["email"] == EMAIL
    assert client.cookies.get("rib_session")
    assert client.get("/api/auth/me").status_code == 200


def test_unknown_email_and_wrong_password_are_indistinguishable(client: TestClient) -> None:
    assert register(client).status_code == 201
    client.cookies.clear()

    wrong_password = client.post(
        "/api/auth/login", json={"email": EMAIL, "password": "definitely wrong"}
    )
    unknown_email = client.post(
        "/api/auth/login", json={"email": "nobody@example.edu", "password": PASSWORD}
    )

    assert wrong_password.status_code == unknown_email.status_code == 401
    assert wrong_password.json() == unknown_email.json()
    assert error_code(wrong_password) == "invalid_credentials"
    assert "Set-Cookie" not in wrong_password.headers


def test_repeated_failures_lock_the_account_with_a_429(client: TestClient) -> None:
    assert register(client).status_code == 201
    client.cookies.clear()

    attempts = [
        client.post("/api/auth/login", json={"email": EMAIL, "password": f"wrong-{index}"})
        for index in range(auth.MAX_FAILED_ATTEMPTS)
    ]
    assert [r.status_code for r in attempts] == [401] * auth.MAX_FAILED_ATTEMPTS

    locked = client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})

    assert locked.status_code == 429
    assert error_code(locked) == "account_locked"
    assert "Set-Cookie" not in locked.headers


def test_me_requires_a_session(client: TestClient) -> None:
    response = client.get("/api/auth/me")

    assert response.status_code == 401
    assert error_code(response) == "invalid_session"
    assert "Set-Cookie" not in response.headers


def test_expired_session_is_rejected(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    assert register(client).status_code == 201
    later = auth._now() + auth.SESSION_LIFETIME + timedelta(minutes=1)
    monkeypatch.setattr(auth, "_now", lambda: later)

    response = client.get("/api/auth/me")

    assert response.status_code == 401
    assert error_code(response) == "invalid_session"


def test_logout_revokes_the_session_and_is_idempotent(client: TestClient) -> None:
    assert register(client).status_code == 201
    token = client.cookies.get("rib_session")

    first = client.post("/api/auth/logout")
    second = client.post("/api/auth/logout")

    assert first.status_code == second.status_code == 204
    assert first.text == ""
    assert "Max-Age=0" in first.headers["set-cookie"]
    assert second.status_code == 204
    assert client.cookies.get("rib_session") is None
    assert client.get("/api/auth/me").status_code == 401

    # Replaying the revoked token must not resurrect the session either.
    replay = client.get("/api/auth/me", headers={"Cookie": f"rib_session={token}"})
    assert replay.status_code == 401


def test_logout_with_an_unknown_cookie_still_clears_it(client: TestClient) -> None:
    response = client.post("/api/auth/logout", headers={"Cookie": "rib_session=not-a-real-token"})

    assert response.status_code == 204
    assert "Max-Age=0" in response.headers["set-cookie"]


def test_secure_flag_follows_configuration(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, cookie_secure=True)
    with TestClient(create_app(settings)) as secure_client:
        response = register(secure_client)

    assert response.status_code == 201
    assert "Secure" in response.headers["set-cookie"]


def test_cookie_name_follows_configuration(tmp_path: Path) -> None:
    settings = make_settings(tmp_path, session_cookie_name="rib_test_session")
    with TestClient(create_app(settings)) as custom_client:
        assert register(custom_client).status_code == 201
        cookie = custom_client.get("/api/auth/me")
        assert custom_client.cookies.get("rib_test_session")
        assert cookie.status_code == 200


# --------------------------------------------------------------------------- #
# consents
# --------------------------------------------------------------------------- #


def test_consent_endpoints_require_a_session(client: TestClient) -> None:
    assert client.get("/api/consents").status_code == 401
    assert client.post("/api/consents", json={"kind": "gmail_access"}).status_code == 401


def test_accept_consent_is_idempotent_and_visible_in_me(client: TestClient) -> None:
    assert register(client).status_code == 201

    first = client.post("/api/consents", json={"kind": "gmail_access"})
    second = client.post("/api/consents", json={"kind": "gmail_access"})

    assert first.status_code == 200
    assert first.json()["already_granted"] is False
    assert second.json()["already_granted"] is True

    states = {item["kind"]: item for item in client.get("/api/consents").json()["consents"]}
    assert states["gmail_access"]["granted"] is True
    assert states["gmail_access"]["text"]

    me = {item["kind"]: item["granted"] for item in client.get("/api/auth/me").json()["consents"]}
    assert me == {"document_processing": True, "gmail_access": True, "personal_data": True}


def test_unknown_consent_kind_is_a_400(client: TestClient) -> None:
    assert register(client).status_code == 201

    response = client.post("/api/consents", json={"kind": "share_with_everyone"})

    assert response.status_code == 400
    assert error_code(response) == "unknown_consent"


def test_one_account_cannot_see_another_accounts_consents(
    settings: Settings,
) -> None:
    app = create_app(settings)
    with TestClient(app) as first, TestClient(app) as second:
        assert register(first, email=EMAIL).status_code == 201
        assert first.post("/api/consents", json={"kind": "gmail_access"}).status_code == 200

        assert register(second, email=OTHER_EMAIL).status_code == 201
        states = {
            item["kind"]: item["granted"] for item in second.get("/api/consents").json()["consents"]
        }

    assert states["gmail_access"] is False
    assert states["personal_data"] is True


# --------------------------------------------------------------------------- #
# request validation and error shape
# --------------------------------------------------------------------------- #


def test_unknown_field_is_rejected_without_echoing_the_password(client: TestClient) -> None:
    response = client.post(
        "/api/auth/login",
        json={"email": EMAIL, "password": PASSWORD, "role": "admin"},
    )

    assert response.status_code == 422
    assert error_code(response) == "invalid_request"
    assert "role" in response.json()["error"]["message"]
    assert PASSWORD not in response.text


def test_missing_field_names_the_field(client: TestClient) -> None:
    response = client.post("/api/auth/login", json={"email": EMAIL})

    assert response.status_code == 422
    assert "password" in response.json()["error"]["message"]


def test_malformed_json_uses_the_same_envelope(client: TestClient) -> None:
    response = client.post(
        "/api/auth/login",
        content=b"{not json",
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 422
    assert error_code(response) == "invalid_request"


def test_unknown_route_and_method_keep_the_error_envelope(client: TestClient) -> None:
    missing = client.get("/api/does-not-exist")
    wrong_method = client.get("/api/auth/login")

    assert missing.status_code == 404
    assert error_code(missing) == "not_found"
    assert wrong_method.status_code == 405
    assert error_code(wrong_method) == "method_not_allowed"


def test_unexpected_failure_returns_a_generic_error(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(*args: object, **kwargs: object) -> None:
        raise RuntimeError(f"secret detail {PASSWORD}")

    monkeypatch.setattr(auth, "authenticate", explode)
    app = create_app(settings)

    with TestClient(app, raise_server_exceptions=False) as failing:
        response = failing.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})

    assert response.status_code == 500
    assert error_code(response) == "internal_error"
    assert PASSWORD not in response.text
    assert "Traceback" not in response.text


def test_database_failure_is_a_503_without_internal_detail(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    def explode(*args: object, **kwargs: object) -> None:
        raise sqlite3.OperationalError("unable to open database file C:/secret/path.db")

    monkeypatch.setattr(auth, "authenticate", explode)
    app = create_app(settings)

    with TestClient(app, raise_server_exceptions=False) as failing:
        response = failing.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})

    assert response.status_code == 503
    assert error_code(response) == "database_unavailable"
    assert "secret" not in response.text


# --------------------------------------------------------------------------- #
# CORS and the origin guard
# --------------------------------------------------------------------------- #


def test_cors_allows_the_configured_origin_only(client: TestClient) -> None:
    allowed = client.get("/api/health", headers={"Origin": ORIGIN})
    denied = client.get("/api/health", headers={"Origin": "http://evil.example"})

    assert allowed.headers["access-control-allow-origin"] == ORIGIN
    assert allowed.headers["access-control-allow-credentials"] == "true"
    assert "access-control-allow-origin" not in denied.headers


def test_cors_preflight_covers_the_json_content_type(client: TestClient) -> None:
    response = client.options(
        "/api/auth/login",
        headers={
            "Origin": ORIGIN,
            "Access-Control-Request-Method": "POST",
            "Access-Control-Request-Headers": "content-type",
        },
    )

    assert response.status_code == 200
    assert response.headers["access-control-allow-origin"] == ORIGIN
    assert "POST" in response.headers["access-control-allow-methods"]


def test_cross_origin_post_is_rejected_before_credentials_are_checked(
    client: TestClient,
) -> None:
    assert register(client).status_code == 201
    client.cookies.clear()

    for _ in range(auth.MAX_FAILED_ATTEMPTS + 2):
        blocked = client.post(
            "/api/auth/login",
            json={"email": EMAIL, "password": "wrong"},
            headers={"Origin": "http://evil.example"},
        )
        assert blocked.status_code == 403
        assert error_code(blocked) == "origin_not_allowed"

    # The guard runs before authentication, so the account must not be locked out
    # by requests that were never processed.
    assert (
        client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD}).status_code
        == 200
    )


def test_same_origin_and_read_requests_are_not_blocked(client: TestClient) -> None:
    same_origin = client.post(
        "/api/auth/login",
        json={"email": EMAIL, "password": PASSWORD},
        headers={"Origin": "http://testserver"},
    )
    cross_origin_get = client.get("/api/health", headers={"Origin": "http://evil.example"})

    assert same_origin.status_code == 401  # reaches authentication, no such account
    assert error_code(same_origin) == "invalid_credentials"
    assert cross_origin_get.status_code == 200


# --------------------------------------------------------------------------- #
# data export and account deletion
# --------------------------------------------------------------------------- #


def test_export_requires_a_session(client: TestClient) -> None:
    response = client.get("/api/auth/export")

    assert response.status_code == 401
    assert error_code(response) == "invalid_session"


def test_export_returns_the_account_structure_without_secrets(client: TestClient) -> None:
    assert register(client).status_code == 201

    response = client.get("/api/auth/export")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert set(body) == {"account", "consents", "profile", "documents", "facts"}
    assert body["account"]["email"] == EMAIL
    assert body["profile"] is None
    assert body["documents"] == []
    assert body["facts"] == []
    assert body["consents"]["personal_data"] is True
    assert PASSWORD not in response.text
    assert "scrypt" not in response.text
    assert "token_hash" not in response.text


def test_delete_requires_the_password_and_keeps_the_account_on_failure(
    client: TestClient,
) -> None:
    assert register(client).status_code == 201

    response = client.request("DELETE", "/api/auth/account", json={"password": "definitely not it"})

    assert response.status_code == 403
    assert error_code(response) == "wrong_password"
    assert client.get("/api/auth/me").status_code == 200


def test_delete_removes_the_account_and_every_session(
    client: TestClient, settings: Settings
) -> None:
    assert register(client).status_code == 201
    token = client.cookies.get("rib_session")

    response = client.request("DELETE", "/api/auth/account", json={"password": PASSWORD})

    assert response.status_code == 204
    assert response.text == ""
    assert "Max-Age=0" in response.headers["set-cookie"]
    assert client.get("/api/auth/me").status_code == 401
    assert client.get("/api/auth/me", headers={"Cookie": f"rib_session={token}"}).status_code == 401
    assert (
        client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD}).status_code
        == 401
    )

    with db.session(settings.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM accounts").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM users").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0
        assert connection.execute("SELECT COUNT(*) FROM consents").fetchone()[0] == 0


def test_authenticated_responses_are_not_cacheable(client: TestClient) -> None:
    assert register(client).status_code == 201

    assert client.get("/api/auth/me").headers["cache-control"] == "no-store"
    assert client.get("/api/consents").headers["cache-control"] == "no-store"


# --------------------------------------------------------------------------- #
# rate limiting and hardening
# --------------------------------------------------------------------------- #


def test_register_is_rate_limited_with_a_retry_after(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        ratelimit,
        "REGISTER_RULE",
        ratelimit.RateLimitRule(name="auth.register", limit=2, window_seconds=3600),
    )

    first = register(client)
    second = register(client, email=OTHER_EMAIL)
    refused = register(client, email="third@example.edu")

    assert [first.status_code, second.status_code, refused.status_code] == [201, 201, 429]
    assert error_code(refused) == "rate_limited"
    retry_after = refused.headers["retry-after"]
    assert retry_after.isdigit()
    assert 1 <= int(retry_after) <= 3600
    assert "third@example.edu" not in refused.text


def test_the_login_rate_limit_runs_before_credentials_are_checked(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        ratelimit,
        "LOGIN_RULE",
        ratelimit.RateLimitRule(name="auth.login", limit=2, window_seconds=900),
    )

    attempts = [
        client.post(
            "/api/auth/login",
            json={"email": f"nobody{index}@example.edu", "password": PASSWORD},
        )
        for index in range(3)
    ]

    assert [response.status_code for response in attempts] == [401, 401, 429]
    assert error_code(attempts[-1]) == "rate_limited"
    assert attempts[-1].headers["retry-after"].isdigit()
    # Neither account state nor a session leaked through the refused request.
    assert "Set-Cookie" not in attempts[-1].headers


def test_register_and_login_responses_are_not_cacheable(client: TestClient) -> None:
    registered = register(client)
    client.cookies.clear()
    logged_in = client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})

    assert registered.status_code == 201
    assert logged_in.status_code == 200
    assert registered.headers["cache-control"] == "no-store"
    assert logged_in.headers["cache-control"] == "no-store"


def test_the_session_cookie_is_host_only(client: TestClient) -> None:
    cookie = register(client).headers["set-cookie"]

    # No `Domain=` attribute: a sibling subdomain must not receive the cookie.
    assert "domain" not in cookie.lower()


def test_the_account_lockout_response_carries_retry_after(client: TestClient) -> None:
    assert register(client).status_code == 201
    client.cookies.clear()
    for index in range(auth.MAX_FAILED_ATTEMPTS):
        assert (
            client.post(
                "/api/auth/login", json={"email": EMAIL, "password": f"wrong-{index}"}
            ).status_code
            == 401
        )

    locked = client.post("/api/auth/login", json={"email": EMAIL, "password": PASSWORD})

    assert locked.status_code == 429
    assert error_code(locked) == "account_locked"
    retry_after = locked.headers["retry-after"]
    assert retry_after.isdigit()
    lockout_seconds = int(auth.LOCKOUT.total_seconds())
    assert 1 <= int(retry_after) <= lockout_seconds


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("POST", "/api/consents", {"kind": "gmail_access"}),
        ("POST", "/api/auth/logout", None),
        ("DELETE", "/api/auth/account", {"password": PASSWORD}),
    ],
)
def test_the_origin_guard_covers_every_state_changing_route(
    client: TestClient, method: str, path: str, body: dict[str, str] | None
) -> None:
    response = client.request(
        method,
        path,
        json=body,
        headers={"Origin": "https://evil.example"},
    )

    assert response.status_code == 403
    assert error_code(response) == "origin_not_allowed"


def test_the_same_origin_with_a_different_scheme_is_refused(client: TestClient) -> None:
    response = client.post(
        "/api/auth/logout",
        headers={"Origin": "https://testserver"},
    )

    assert response.status_code == 403
    assert error_code(response) == "origin_not_allowed"


def test_a_failed_document_purge_keeps_the_account_and_can_be_retried(
    client: TestClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    registered = register(client)
    user_id = registered.json()["account"]["user_id"]
    with db.session(settings.database_path) as connection:
        stored = document_service.save_document(
            connection,
            user_id=user_id,
            display_name="cv.txt",
            payload=b"a plain text curriculum vitae",
            data_directory=settings.data_directory,
            actor=f"user:{user_id}",
        )
    document_path = settings.data_directory / "documents" / stored.storage_path
    assert document_path.is_file()

    original_unlink = Path.unlink

    def locked_unlink(self: Path, *args: object, **kwargs: object) -> None:
        if self == document_path:
            raise PermissionError("the file is open in another program")
        original_unlink(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "unlink", locked_unlink)

    refused = client.request("DELETE", "/api/auth/account", json={"password": PASSWORD})

    assert refused.status_code == 409
    assert error_code(refused) == "deletion_incomplete"
    assert str(settings.data_directory) not in refused.text
    # Nothing was half-deleted: the account and its document still exist.
    assert client.get("/api/auth/me").status_code == 200
    with db.session(settings.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM student_documents").fetchone()[0] == 1

    monkeypatch.undo()
    retried = client.request("DELETE", "/api/auth/account", json={"password": PASSWORD})

    assert retried.status_code == 204
    assert not document_path.exists()
    assert client.get("/api/auth/me").status_code == 401
