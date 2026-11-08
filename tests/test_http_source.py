from __future__ import annotations

from urllib.request import Request

import pytest

from research_inter_bridge.errors import ConfigError, SourceFetchError
from research_inter_bridge.http_source import (
    DEFAULT_MAX_BYTES,
    PoliteHttpClient,
    SourceResponse,
    decode_text,
)


class FakeTransport:
    """Records the request and returns a prepared response; never opens a socket."""

    def __init__(self, response: SourceResponse | None = None, error: Exception | None = None):
        self.response = response
        self.error = error
        self.request: Request | None = None
        self.timeout: float | None = None
        self.calls = 0

    def send(self, request: Request, timeout: float) -> SourceResponse:
        self.calls += 1
        self.request = request
        self.timeout = timeout
        if self.error is not None:
            raise self.error
        assert self.response is not None
        return self.response


def make_client(transport: FakeTransport, **overrides: object) -> PoliteHttpClient:
    options: dict[str, object] = {
        "contact_email": "research@example.org",
        "timeout_seconds": 12.5,
        "transport": transport,
    }
    options.update(overrides)
    return PoliteHttpClient(**options)  # type: ignore[arg-type]


def ok(body: bytes = b"<html></html>") -> SourceResponse:
    return SourceResponse(
        status=200,
        url="https://example.edu/programs",
        content_type="text/html; charset=utf-8",
        body=body,
    )


def test_client_requires_a_contact_email_before_any_request() -> None:
    with pytest.raises(ConfigError) as excinfo:
        PoliteHttpClient("", transport=FakeTransport(ok()))

    message = str(excinfo.value)
    assert "RIB_RESEARCH_CONTACT_EMAIL" in message
    assert "email address" in message


def test_client_rejects_a_display_name_instead_of_one_address() -> None:
    with pytest.raises(ConfigError):
        PoliteHttpClient("Research Team <research@example.org>", transport=FakeTransport(ok()))


def test_request_identifies_the_client_with_a_contact_and_timeout() -> None:
    transport = FakeTransport(ok())
    client = make_client(transport)

    client.get("https://example.edu/programs")

    assert transport.request is not None
    assert transport.request.get_header("User-agent") == (f"{client.user_agent}")
    assert "ResearchInterBridge/" in client.user_agent
    assert "research@example.org" in client.user_agent
    assert transport.request.get_header("From") == "research@example.org"
    assert transport.request.get_header("Accept") == (
        "text/html,application/xhtml+xml,application/json;q=0.9"
    )
    assert transport.timeout == 12.5
    assert transport.calls == 1


def test_custom_user_agent_is_kept_exactly() -> None:
    transport = FakeTransport(ok())
    client = make_client(transport, user_agent="TestAgent/1 (+mailto:research@example.org)")

    client.get("https://example.edu/programs")

    assert transport.request is not None
    assert transport.request.get_header("User-agent") == (
        "TestAgent/1 (+mailto:research@example.org)"
    )


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.edu/programs",
        "file:///etc/passwd",
        "https://user:secret@example.edu/programs",
        "https:///programs",
    ],
)
def test_refuses_unsafe_or_incomplete_urls(url: str) -> None:
    transport = FakeTransport(ok())

    with pytest.raises(SourceFetchError):
        make_client(transport).get(url)

    assert transport.calls == 0


def test_non_success_status_is_an_error_and_the_body_is_not_echoed() -> None:
    transport = FakeTransport(
        SourceResponse(
            status=503,
            url="https://example.edu/programs",
            content_type="text/html",
            body=b"private page text that must not become an error message",
        )
    )

    with pytest.raises(SourceFetchError) as excinfo:
        make_client(transport).get("https://example.edu/programs")

    message = str(excinfo.value)
    assert "example.edu" in message
    assert "503" in message
    assert "private page text" not in message


def test_oversized_response_is_rejected_before_parsing() -> None:
    transport = FakeTransport(ok(b"x" * 11))

    with pytest.raises(SourceFetchError) as excinfo:
        make_client(transport, max_bytes=10).get("https://example.edu/programs")

    assert "more than 10 bytes" in str(excinfo.value)


def test_redirect_to_a_non_http_scheme_is_rejected() -> None:
    transport = FakeTransport(
        SourceResponse(
            status=200,
            url="ftp://example.edu/programs",
            content_type="text/html",
            body=b"<html></html>",
        )
    )

    with pytest.raises(SourceFetchError):
        make_client(transport).get("https://example.edu/programs")


def test_transport_failure_is_wrapped_with_the_host_only() -> None:
    transport = FakeTransport(error=TimeoutError("timed out"))

    with pytest.raises(SourceFetchError) as excinfo:
        make_client(transport).get("https://example.edu/programs")

    assert "example.edu" in str(excinfo.value)
    assert "timed out" in str(excinfo.value)


def test_default_size_cap_is_bounded() -> None:
    assert DEFAULT_MAX_BYTES > 0
    assert DEFAULT_MAX_BYTES <= 10 * 1024 * 1024


def test_decode_text_honours_the_declared_charset() -> None:
    response = SourceResponse(
        status=200,
        url="https://example.edu/programs",
        content_type='text/html; charset="iso-8859-1"',
        body="café".encode("iso-8859-1"),
    )

    assert decode_text(response) == "café"


def test_decode_text_falls_back_when_the_charset_is_unknown() -> None:
    response = SourceResponse(
        status=200,
        url="https://example.edu/programs",
        content_type="text/html; charset=not-a-real-charset",
        body="café".encode(),
    )

    assert decode_text(response) == "café"


def test_decode_text_replaces_bad_bytes_instead_of_failing_the_page() -> None:
    response = SourceResponse(
        status=200,
        url="https://example.edu/programs",
        content_type="text/html",
        body=b"ok \xff",
    )

    assert decode_text(response).startswith("ok ")
