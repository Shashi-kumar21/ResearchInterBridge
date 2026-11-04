"""Application factory for the JSON API (ADR-012).

``create_app`` takes settings instead of reading the environment so tests and
the CLI can build an app rooted at a temporary database. The lifespan applies
migrations once per process; every request then reuses the migrated schema.
"""

from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, status
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from .. import db
from ..config import Settings, load_settings
from ..errors import AuthError, ConsentError, DocumentPurgeError, RateLimitError
from ..logging import get_logger
from .middleware import OriginGuardMiddleware
from .routes import AUTH_STATUS_CODES, CONSENT_STATUS_CODES, router

logger = get_logger(__name__)

HTTP_CODES = {
    status.HTTP_400_BAD_REQUEST: "bad_request",
    status.HTTP_401_UNAUTHORIZED: "unauthorized",
    status.HTTP_403_FORBIDDEN: "forbidden",
    status.HTTP_404_NOT_FOUND: "not_found",
    status.HTTP_405_METHOD_NOT_ALLOWED: "method_not_allowed",
    status.HTTP_409_CONFLICT: "conflict",
    status.HTTP_429_TOO_MANY_REQUESTS: "too_many_requests",
}


def error_response(
    status_code: int,
    code: str,
    message: str,
    *,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    """Every failure uses one envelope: ``{"error": {"code", "message"}}``.

    ``headers`` carries protocol hints a client needs to act on the failure, such
    as ``Retry-After``; it never carries anything derived from request content.
    """

    return JSONResponse(
        {"error": {"code": code, "message": message}},
        status_code=status_code,
        headers=headers,
    )


def _validation_message(exc: RequestValidationError) -> str:
    """Describe a bad body without echoing any of its values.

    Pydantic errors carry the offending input. Returning it would put a rejected
    password in the HTTP response and in the caller's logs, so only the field
    path and the reason are kept.
    """

    parts: list[str] = []
    for error in exc.errors():
        location = ".".join(str(part) for part in error.get("loc", ()) if part != "body")
        reason = str(error.get("msg", "invalid value"))
        parts.append(f"{location or 'body'}: {reason}")
    return "; ".join(parts) or "request body is invalid."


def create_app(settings: Settings | None = None) -> FastAPI:
    """Build the API application for the given settings (default: environment)."""

    resolved = settings if settings is not None else load_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Fail at startup, not on the first request: a missing or unwritable
        # database should stop the process with an actionable traceback.
        with db.session(resolved.database_path) as connection:
            version = db.apply_migrations(connection)
        logger.info("api started schema_version=%s", version)
        yield

    app = FastAPI(
        title="ResearchInterBridge API",
        version="0.1.0",
        description=(
            "Thin HTTP layer over the same services the CLI uses. No endpoint sends email."
        ),
        lifespan=lifespan,
    )
    app.state.settings = resolved

    # Added first so CORSMiddleware wraps it: an error produced by the guard must
    # still carry CORS headers, otherwise the browser shows a network error
    # instead of the 403 the API meant to send.
    app.add_middleware(OriginGuardMiddleware, allowed_origins=resolved.cors_origins)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(resolved.cors_origins),
        allow_credentials=True,
        allow_methods=["GET", "POST", "DELETE", "OPTIONS"],
        allow_headers=["Content-Type"],
        # A browser only exposes simple response headers to JavaScript. The site
        # reads Retry-After to tell a student how long a 429 wait is, so it must
        # be listed explicitly; exposing it leaks nothing else.
        expose_headers=["Retry-After"],
        max_age=600,
    )

    app.include_router(router)
    _install_error_handlers(app)
    return app


def _install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(RequestValidationError)
    async def _invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        return error_response(
            status.HTTP_422_UNPROCESSABLE_ENTITY,
            "invalid_request",
            _validation_message(exc),
        )

    @app.exception_handler(AuthError)
    async def _auth_error(request: Request, exc: AuthError) -> JSONResponse:
        status_code = AUTH_STATUS_CODES.get(exc.code, status.HTTP_400_BAD_REQUEST)
        headers = {"Retry-After": str(exc.retry_after)} if exc.retry_after is not None else None
        return error_response(status_code, exc.code, str(exc), headers=headers)

    @app.exception_handler(RateLimitError)
    async def _rate_limited(request: Request, exc: RateLimitError) -> JSONResponse:
        return error_response(
            status.HTTP_429_TOO_MANY_REQUESTS,
            exc.code,
            str(exc),
            headers={"Retry-After": str(exc.retry_after)},
        )

    @app.exception_handler(DocumentPurgeError)
    async def _purge_failed(request: Request, exc: DocumentPurgeError) -> JSONResponse:
        # Rows are still present, so the account is intact and the student can
        # retry once the operating system releases the file.
        return error_response(
            status.HTTP_409_CONFLICT,
            "deletion_incomplete",
            str(exc),
        )

    @app.exception_handler(ConsentError)
    async def _consent_error(request: Request, exc: ConsentError) -> JSONResponse:
        status_code = CONSENT_STATUS_CODES.get(exc.code, status.HTTP_400_BAD_REQUEST)
        return error_response(status_code, exc.code, str(exc))

    @app.exception_handler(StarletteHTTPException)
    async def _http_error(request: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = HTTP_CODES.get(exc.status_code, "http_error")
        message = exc.detail if isinstance(exc.detail, str) else "request failed."
        return error_response(exc.status_code, code, message, headers=exc.headers)

    @app.exception_handler(sqlite3.Error)
    async def _database_error(request: Request, exc: sqlite3.Error) -> JSONResponse:
        # The message is logged for the operator and never returned: it can name
        # the database file or an internal table.
        logger.exception("database error while handling %s", request.url.path)
        return error_response(
            status.HTTP_503_SERVICE_UNAVAILABLE,
            "database_unavailable",
            "the database is unavailable; try again shortly.",
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception) -> JSONResponse:
        logger.exception("unhandled API error on %s", request.url.path)
        return error_response(
            status.HTTP_500_INTERNAL_SERVER_ERROR,
            "internal_error",
            "internal server error.",
        )
