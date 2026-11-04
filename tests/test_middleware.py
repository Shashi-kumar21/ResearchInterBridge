"""Origin-guard decisions: exact scheme, host and port, and no invented origin."""

from __future__ import annotations

import pytest

from research_inter_bridge.api.middleware import same_origin


@pytest.mark.parametrize(
    ("origin", "host"),
    [
        ("http://app.example.edu", "app.example.edu"),
        ("http://APP.example.EDU", "app.example.edu"),
        ("http://app.example.edu:80", "app.example.edu:80"),
    ],
)
def test_the_requests_own_origin_matches(origin: str, host: str) -> None:
    assert same_origin(origin, "http", host) is True


def test_a_port_that_differs_is_a_different_origin() -> None:
    assert same_origin("http://app.example.edu:3000", "http", "app.example.edu:8080") is False


def test_the_same_host_and_port_matches() -> None:
    assert same_origin("https://app.example.edu:8443", "https", "app.example.edu:8443") is True


def test_a_different_scheme_is_a_different_origin() -> None:
    # A TLS-terminating proxy must tell the app the original scheme; otherwise
    # an https browser origin genuinely does not match the http request scope.
    assert same_origin("https://app.example.edu", "http", "app.example.edu") is False
    assert same_origin("http://app.example.edu", "https", "app.example.edu") is False


@pytest.mark.parametrize("origin", ["null", "", "file://app.example.edu", "app.example.edu"])
def test_an_origin_that_is_not_an_http_origin_never_matches(origin: str) -> None:
    assert same_origin(origin, "http", "app.example.edu") is False


@pytest.mark.parametrize("host", [None, ""])
def test_a_request_without_a_host_header_never_matches(host: str | None) -> None:
    assert same_origin("http://app.example.edu", "http", host) is False
