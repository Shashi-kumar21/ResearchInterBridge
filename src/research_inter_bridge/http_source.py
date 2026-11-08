"""Polite HTTP access to public research sources.

Discovery fetches public pages from universities and scholarly providers. The
rules here are provider-agnostic and deliberately small:

* a descriptive ``User-Agent`` with a reachable contact address, plus the same
  address in ``From``, so an operator can be reached about traffic;
* a timeout and a hard response-size cap, so one page cannot exhaust memory;
* only ``http``/``https`` URLs without embedded credentials, and no scheme
  downgrade through a redirect;
* failures become :class:`SourceFetchError` naming the host and status. The
  response body is never part of the message: it is untrusted text and may
  contain private data.

Tests inject a transport, so the suite never opens a socket. The default
transport uses the standard library only, keeping the runtime dependency set
unchanged.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

from . import __version__
from .errors import ConfigError, SourceFetchError

#: One HTML page should be far smaller than this. The cap also bounds a
#: misconfigured JSON endpoint returning an unbounded payload.
DEFAULT_MAX_BYTES = 2 * 1024 * 1024

DEFAULT_ACCEPT = "text/html,application/xhtml+xml,application/json;q=0.9"

_ALLOWED_SCHEMES = frozenset({"http", "https"})
_CHARSET_RE = re.compile(r"charset\s*=\s*[\"']?([\w.+-]+)", re.IGNORECASE)
_CONTACT_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


@dataclass(frozen=True, slots=True)
class SourceResponse:
    """One HTTP response, before the caller decides how to parse it."""

    status: int
    url: str
    content_type: str
    body: bytes


class Transport(Protocol):
    """The network boundary, injectable so tests stay offline."""

    def send(self, request: Request, timeout: float) -> SourceResponse:
        """Return one response or raise :class:`SourceFetchError`."""


class UrllibTransport:
    """Default transport: standard-library ``urllib`` with a size cap."""

    def __init__(self, max_bytes: int = DEFAULT_MAX_BYTES) -> None:
        self._max_bytes = max_bytes

    def send(self, request: Request, timeout: float) -> SourceResponse:
        try:
            with urlopen(request, timeout=timeout) as response:
                return SourceResponse(
                    status=response.status,
                    url=response.geturl(),
                    content_type=response.headers.get("Content-Type", ""),
                    body=response.read(self._max_bytes + 1),
                )
        except HTTPError as exc:
            # An error page is still a response the caller can classify by
            # status; read only the bounded prefix and never surface it.
            return SourceResponse(
                status=exc.code,
                url=exc.geturl() or request.full_url,
                content_type=exc.headers.get("Content-Type", "") if exc.headers else "",
                body=exc.read(self._max_bytes + 1),
            )
        except (URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise SourceFetchError(
                f"could not reach {urlsplit(request.full_url).netloc}: {reason}"
            ) from exc


class PoliteHttpClient:
    """A small GET-only client that identifies itself to a source.

    Constructing the client requires a contact email. That is deliberate: a
    live fetch without a reachable contact is not polite, so the configuration
    error happens before any request is made.
    """

    def __init__(
        self,
        contact_email: str,
        *,
        timeout_seconds: float = 15.0,
        max_bytes: int = DEFAULT_MAX_BYTES,
        transport: Transport | None = None,
        user_agent: str | None = None,
    ) -> None:
        contact = (contact_email or "").strip()
        if not _CONTACT_RE.match(contact):
            raise ConfigError(
                "RIB_RESEARCH_CONTACT_EMAIL must be one email address; "
                "set it before contacting a live research source."
            )
        if timeout_seconds <= 0:
            raise ConfigError(
                f"the source timeout must be greater than 0; got {timeout_seconds!r}."
            )
        if max_bytes <= 0:
            raise ConfigError(f"the source size cap must be greater than 0; got {max_bytes!r}.")
        self._contact = contact
        self._timeout = timeout_seconds
        self._max_bytes = max_bytes
        self._transport: Transport = transport or UrllibTransport(max_bytes=max_bytes)
        self._user_agent = user_agent or (f"ResearchInterBridge/{__version__} (+mailto:{contact})")

    @property
    def user_agent(self) -> str:
        """The exact value sent to a source, exposed for diagnostics and tests."""

        return self._user_agent

    def get(self, url: str, *, accept: str = DEFAULT_ACCEPT) -> SourceResponse:
        """Fetch ``url`` once and return a bounded, successful response.

        Non-2xx responses raise :class:`SourceFetchError`; the caller cannot
        mistake an error page for a program page.
        """

        parts = urlsplit(url)
        if parts.scheme not in _ALLOWED_SCHEMES:
            raise SourceFetchError(
                f"refusing to fetch {url!r}: only http and https URLs are allowed."
            )
        if not parts.netloc or parts.username or parts.password:
            raise SourceFetchError(
                f"refusing to fetch {url!r}: a host is required and credentials are not allowed."
            )

        request = Request(
            url,
            headers={
                "User-Agent": self._user_agent,
                "From": self._contact,
                "Accept": accept,
            },
        )
        try:
            response = self._transport.send(request, self._timeout)
        except SourceFetchError:
            raise
        except (URLError, TimeoutError, OSError) as exc:
            reason = getattr(exc, "reason", exc)
            raise SourceFetchError(f"could not reach {parts.netloc}: {reason}") from exc

        if len(response.body) > self._max_bytes:
            raise SourceFetchError(
                f"{parts.netloc} returned more than {self._max_bytes} bytes; "
                "raise the cap only for a source that documents a larger page."
            )
        if not 200 <= response.status < 300:
            raise SourceFetchError(
                f"{parts.netloc} returned HTTP {response.status}; the source was not "
                "read as a program page."
            )
        final = urlsplit(response.url)
        if final.scheme not in _ALLOWED_SCHEMES:
            raise SourceFetchError(
                f"{parts.netloc} redirected to a {final.scheme or 'unknown'} URL; "
                "only http and https are accepted."
            )
        return response


def decode_text(response: SourceResponse) -> str:
    """Decode a response body using its declared charset, falling back to UTF-8.

    ``errors="replace"`` keeps one bad byte from breaking a whole page; the
    parser only reads explicit structured data, so replacement characters cannot
    become a deadline or a name.
    """

    match = _CHARSET_RE.search(response.content_type)
    encoding = match.group(1) if match else "utf-8"
    try:
        return response.body.decode(encoding, errors="replace")
    except LookupError:
        # An unknown charset name is a source bug, not a reason to fail a page.
        return response.body.decode("utf-8", errors="replace")
