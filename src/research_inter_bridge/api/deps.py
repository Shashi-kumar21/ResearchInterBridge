"""FastAPI dependencies: settings, database connection, session and cookies.

One SQLite connection per request, opened and closed by :func:`get_connection`.
The connection is never shared between requests, so a slow request cannot hold a
transaction that another request waits on.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request, Response

from .. import auth, consent, db
from ..config import Settings
from ..errors import AuthError
from .schemas import ConsentStateOut


def get_settings(request: Request) -> Settings:
    """Return the settings the application was created with."""

    settings: Settings = request.app.state.settings
    return settings


def get_connection(request: Request) -> Iterator[sqlite3.Connection]:
    """Yield a request-scoped connection and always close it."""

    settings = get_settings(request)
    with db.session(settings.database_path) as connection:
        yield connection


SettingsDep = Annotated[Settings, Depends(get_settings)]
Connection = Annotated[sqlite3.Connection, Depends(get_connection)]


def session_token(request: Request) -> str:
    """Read the session cookie, failing with the same error as an expired one."""

    settings = get_settings(request)
    token = request.cookies.get(settings.session_cookie_name)
    if not token:
        raise AuthError("authentication required.", code="invalid_session")
    return token


Token = Annotated[str, Depends(session_token)]


def current_session(token: Token, connection: Connection) -> auth.Session:
    """Resolve the cookie token to a live session."""

    return auth.resolve_session(connection, token)


CurrentSession = Annotated[auth.Session, Depends(current_session)]


def current_account(session: CurrentSession, connection: Connection) -> auth.Account:
    """Return the account behind the current session."""

    return auth.get_account(connection, session.user_id)


CurrentAccount = Annotated[auth.Account, Depends(current_account)]


def consent_states(connection: sqlite3.Connection, user_id: int) -> list[ConsentStateOut]:
    """Every known consent kind with its current-policy state."""

    status = consent.consent_status(connection, user_id)
    return [
        ConsentStateOut(kind=kind, text=consent.CONSENT_TEXTS[kind], granted=granted)
        for kind, granted in status.items()
    ]


def set_session_cookie(response: Response, settings: Settings, token: str) -> None:
    """Attach the session cookie.

    ``HttpOnly`` keeps it away from JavaScript, ``SameSite=Lax`` keeps it off
    cross-site form posts, and ``Path=/`` matches the whole API. The cookie is
    host-only: no ``Domain`` attribute, so a sibling subdomain cannot receive it.
    ``Secure`` is configuration because plain-HTTP localhost cannot set it.
    """

    response.set_cookie(
        key=settings.session_cookie_name,
        value=token,
        max_age=int(auth.SESSION_LIFETIME.total_seconds()),
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response, settings: Settings) -> None:
    """Ask the browser to drop the session cookie, matching how it was set."""

    response.delete_cookie(
        key=settings.session_cookie_name,
        path="/",
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
    )
