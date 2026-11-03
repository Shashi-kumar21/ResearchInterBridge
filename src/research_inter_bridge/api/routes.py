"""HTTP routes. Every rule they apply lives in the service modules."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Request, Response, status

from .. import auth, consent
from ..errors import AuthError
from ..health import check_health
from .deps import (
    Connection,
    CurrentAccount,
    CurrentSession,
    SettingsDep,
    clear_session_cookie,
    consent_states,
    set_session_cookie,
)
from .schemas import (
    AccountOut,
    ConsentAcceptedOut,
    ConsentCatalogueItemOut,
    ConsentCatalogueOut,
    ConsentListOut,
    ConsentRequest,
    DeleteAccountRequest,
    HealthOut,
    LoginRequest,
    MeOut,
    RegisterRequest,
    SessionOut,
)

router = APIRouter(prefix="/api")

#: Stable error codes -> HTTP status. Kept next to the routes so a new service
#: error cannot be added without deciding what it means over HTTP.
AUTH_STATUS_CODES: dict[str, int] = {
    "invalid_input": status.HTTP_400_BAD_REQUEST,
    "invalid_credentials": status.HTTP_401_UNAUTHORIZED,
    "invalid_session": status.HTTP_401_UNAUTHORIZED,
    "account_locked": status.HTTP_429_TOO_MANY_REQUESTS,
    "wrong_password": status.HTTP_403_FORBIDDEN,
    "email_taken": status.HTTP_409_CONFLICT,
    "account_missing": status.HTTP_404_NOT_FOUND,
    "internal_error": status.HTTP_500_INTERNAL_SERVER_ERROR,
}

CONSENT_STATUS_CODES: dict[str, int] = {
    "unknown_consent": status.HTTP_400_BAD_REQUEST,
    "consent_required": status.HTTP_403_FORBIDDEN,
}


def _account_out(account: auth.Account) -> AccountOut:
    return AccountOut(
        user_id=account.user_id,
        email=account.email,
        created_at=account.created_at,
    )


@router.get("/health", response_model=HealthOut, tags=["system"])
def health(settings: SettingsDep, response: Response) -> HealthOut:
    """Read-only readiness probe.

    Unauthenticated by design (a monitor has no account) and deliberately narrow:
    it reports schema state and which checks passed, never paths or exception
    text. The CLI ``health`` command prints the full report for an operator.
    """

    report = check_health(settings)
    if not report.ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthOut(
        ok=report.ok,
        schema_version=report.schema_version,
        expected_schema_version=report.expected_schema_version,
        checks=dict(report.checks),
    )


@router.get("/consents/catalogue", response_model=ConsentCatalogueOut, tags=["consent"])
def consent_catalogue() -> ConsentCatalogueOut:
    """Public consent texts, so the registration screen renders our wording.

    No user data and no session are involved: this is the same text the CLI
    prints and the database records against a policy version.
    """

    return ConsentCatalogueOut(
        policy_version=consent.POLICY_VERSION,
        consents=[
            ConsentCatalogueItemOut(
                kind=kind,
                text=text,
                required_for_registration=kind in consent.REQUIRED_CONSENTS,
            )
            for kind, text in sorted(consent.CONSENT_TEXTS.items())
        ],
    )


@router.post(
    "/auth/register",
    response_model=SessionOut,
    status_code=status.HTTP_201_CREATED,
    tags=["auth"],
)
def register(
    payload: RegisterRequest,
    response: Response,
    connection: Connection,
    settings: SettingsDep,
) -> SessionOut:
    """Create an account and sign it in.

    Consent is required *before* the account exists, so the service refuses an
    incomplete registration rather than creating a half-consented account.
    """

    account = auth.register(
        connection,
        email=payload.email,
        password=payload.password,
        consents=set(payload.consents),
    )
    session = auth.create_session(connection, user_id=account.user_id)
    if session.token is None:  # pragma: no cover - create_session always returns one
        raise AuthError("session token was not created.", code="internal_error")
    set_session_cookie(response, settings, session.token)
    return SessionOut(account=_account_out(account), expires_at=session.expires_at)


@router.post("/auth/login", response_model=SessionOut, tags=["auth"])
def login(
    payload: LoginRequest,
    response: Response,
    connection: Connection,
    settings: SettingsDep,
) -> SessionOut:
    """Verify credentials and set the session cookie.

    Unknown email, wrong password and a locked account are distinct only in the
    status code and ``code`` field; the two credential failures share one message
    so this endpoint cannot be used to enumerate registered addresses.
    """

    session = auth.authenticate(connection, email=payload.email, password=payload.password)
    account = auth.get_account(connection, session.user_id)
    if session.token is None:  # pragma: no cover - authenticate always returns one
        raise AuthError("session token was not created.", code="internal_error")
    set_session_cookie(response, settings, session.token)
    return SessionOut(account=_account_out(account), expires_at=session.expires_at)


@router.post(
    "/auth/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    tags=["auth"],
)
def logout(
    request: Request,
    response: Response,
    connection: Connection,
    settings: SettingsDep,
) -> None:
    """Revoke the current session and clear the cookie.

    Idempotent: a missing, expired or already-revoked cookie is not an error, and
    the cookie is cleared in every case so a stale token cannot linger.
    """

    token = request.cookies.get(settings.session_cookie_name)
    if token:
        auth.revoke_session(connection, token)
    clear_session_cookie(response, settings)


@router.get("/auth/me", response_model=MeOut, tags=["auth"])
def me(
    session: CurrentSession,
    account: CurrentAccount,
    connection: Connection,
    response: Response,
) -> MeOut:
    """Describe the signed-in account and its consent state."""

    response.headers["Cache-Control"] = "no-store"
    return MeOut(
        account=_account_out(account),
        expires_at=session.expires_at,
        consents=consent_states(connection, account.user_id),
    )


@router.get("/auth/export", response_model=dict[str, Any], tags=["auth"])
def export_data(
    account: CurrentAccount,
    connection: Connection,
    response: Response,
) -> dict[str, Any]:
    """Everything this instance holds for the signed-in account.

    Excludes password hashes, session tokens and document file contents: the
    export is the same structure the CLI and the deletion guarantee describe.
    """

    response.headers["Cache-Control"] = "no-store"
    return auth.export_account_data(connection, account.user_id)


@router.delete(
    "/auth/account",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    tags=["auth"],
)
def delete_account(
    payload: DeleteAccountRequest,
    account: CurrentAccount,
    connection: Connection,
    settings: SettingsDep,
    response: Response,
) -> None:
    """Delete the account and its data, after re-checking the password.

    The password is required again because a stolen session must not be enough
    to destroy a student's work. Deletion removes profile rows, stored documents
    (files included), facts, consents and every session, then clears the cookie.
    """

    auth.delete_account(
        connection,
        user_id=account.user_id,
        password=payload.password,
        data_directory=settings.data_directory,
    )
    clear_session_cookie(response, settings)


@router.get("/consents", response_model=ConsentListOut, tags=["consent"])
def list_consents(
    account: CurrentAccount, connection: Connection, response: Response
) -> ConsentListOut:
    """Consent state for the signed-in account."""

    response.headers["Cache-Control"] = "no-store"
    return ConsentListOut(
        policy_version=consent.POLICY_VERSION,
        consents=consent_states(connection, account.user_id),
    )


@router.post("/consents", response_model=ConsentAcceptedOut, tags=["consent"])
def accept_consent(
    payload: ConsentRequest,
    account: CurrentAccount,
    connection: Connection,
) -> ConsentAcceptedOut:
    """Record one consent for the signed-in account.

    Recording is idempotent, so a retried request reports
    ``already_granted: true`` instead of failing or writing a duplicate row. The
    service writes the single audit row, so this route does not add another.
    """

    created = consent.record_consent(
        connection,
        user_id=account.user_id,
        kind=payload.kind,
        actor=f"user:{account.user_id}",
    )
    return ConsentAcceptedOut(
        policy_version=consent.POLICY_VERSION,
        kind=payload.kind,
        already_granted=not created,
    )
