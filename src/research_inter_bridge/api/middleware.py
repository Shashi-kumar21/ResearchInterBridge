"""Origin guard for state-changing requests.

The session cookie is ``SameSite=Lax``, which already keeps it off cross-site
form posts. This middleware is the second lock: if a browser sends an ``Origin``
header on a method that changes state, the origin must be either the request's
own origin or one the operator allow-listed for CORS. A request without an
``Origin`` header (curl, a server-side client) has no ambient cookie to abuse and
is left alone; the session cookie itself is still required.
"""

from __future__ import annotations

import json
from urllib.parse import urlsplit

from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

#: Methods a browser can issue cross-site without a CORS preflight, but that
#: still act with the user's cookie, are exactly the ones guarded here.
SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS", "TRACE"})


def same_origin(origin: str, scheme: str, host: str | None) -> bool:
    """True when ``origin`` is exactly the origin the request was addressed to.

    Scheme is compared as well as host:port. An ``https`` origin and a request
    that reached the app over ``http`` are different origins, and the app cannot
    tell a TLS-terminating proxy from an on-path attacker without being told.
    A deployment behind such a proxy must configure the server to trust its
    ``Forwarded``/``X-Forwarded-*`` headers (for uvicorn: ``--proxy-headers`` and
    ``--forwarded-allow-ips``); otherwise ``https`` browser requests are refused.
    ``Origin: null`` (a sandboxed frame or a file:// page) never matches.
    """

    if not host:
        return False
    parts = urlsplit(origin)
    return (
        parts.scheme in {"http", "https"}
        and parts.scheme == scheme.lower()
        and parts.netloc.lower() == host.lower()
    )


class OriginGuardMiddleware:
    """Reject cross-origin unsafe requests with the API's usual error shape."""

    def __init__(self, app: ASGIApp, *, allowed_origins: tuple[str, ...] = ()) -> None:
        self.app = app
        self.allowed_origins = frozenset(allowed_origins)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["method"] in SAFE_METHODS:
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        origin = headers.get("origin")
        if origin is None:
            await self.app(scope, receive, send)
            return

        if origin in self.allowed_origins or same_origin(
            origin, scope.get("scheme", "http"), headers.get("host")
        ):
            await self.app(scope, receive, send)
            return

        body = json.dumps(
            {
                "error": {
                    "code": "origin_not_allowed",
                    "message": "this origin may not make state-changing requests to the API.",
                }
            }
        ).encode("utf-8")
        await send(
            {
                "type": "http.response.start",
                "status": 403,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode("ascii")),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
