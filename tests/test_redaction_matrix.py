"""Table-driven redaction matrix (QA follow-ups for M1, M2, M3).

Every payload is pushed through all four logging channels that can leak a
credential - a plain message, ``%``-style arguments, :func:`log_safe` fields and
an exception traceback - so a future pattern change cannot regress silently.
"""

from __future__ import annotations

import io
import json
import logging
from collections.abc import Callable

import pytest

from research_inter_bridge.logging import (
    REDACTED,
    configure_logging,
    get_logger,
    log_safe,
)

GOOGLE_ACCESS_TOKEN = "ya29.A0SECRETTOKEN1234567890"
GOOGLE_REFRESH_TOKEN = "1//0gREFRESHTOKENVALUE123"
GITHUB_TOKEN = "ghp_" + "a" * 36
OPENAI_KEY = "sk-" + "b" * 32
BASIC_VALUE = "dXNlcjpwYXNzd29yZA=="
JWT_VALUE = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9"

# (label, payload, secrets that must disappear; empty means "leave it alone")
CASES: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("bearer_header", f"Authorization: Bearer {JWT_VALUE}", (JWT_VALUE,)),
    ("basic_header", f"Authorization: Basic {BASIC_VALUE}", (BASIC_VALUE,)),
    ("digest_header", "Authorization: Digest abc123def456xyz", ("abc123def456xyz",)),
    ("basic_without_key", f"basic {BASIC_VALUE}", (BASIC_VALUE,)),
    ("json_object", '{"token": "hunter2"}', ("hunter2",)),
    ("json_nested", json.dumps({"auth": {"client_secret": "s3cr3t"}}, indent=2), ("s3cr3t",)),
    ("dict_repr", "{'password': 'p@ssw0rd'}", ("p@ssw0rd",)),
    ("quoted_passphrase", "password='two words'", ("two words",)),
    (
        "google_access",
        f"https://x.example/y?access_token={GOOGLE_ACCESS_TOKEN}",
        (GOOGLE_ACCESS_TOKEN,),
    ),
    ("google_refresh", f"refresh {GOOGLE_REFRESH_TOKEN}", (GOOGLE_REFRESH_TOKEN,)),
    ("github", f"github {GITHUB_TOKEN}", (GITHUB_TOKEN,)),
    ("openai", f"openai {OPENAI_KEY}", (OPENAI_KEY,)),
    ("cookie", "Cookie: sessionid=abc123secret", ("abc123secret",)),
    ("unrelated", "professor_id=42 outcome=success", ()),
)


def _capture(write: Callable[[logging.Logger], None]) -> str:
    stream = io.StringIO()
    configure_logging("DEBUG", stream=stream)
    write(get_logger("research_inter_bridge.tests.matrix"))
    return stream.getvalue()


@pytest.mark.parametrize(("label", "payload", "secrets"), CASES, ids=[c[0] for c in CASES])
def test_message_redaction_matrix(label: str, payload: str, secrets: tuple[str, ...]) -> None:
    output = _capture(lambda logger: logger.info(payload))

    for secret in secrets:
        assert secret not in output, f"{label}: secret leaked through the message"
    if secrets:
        assert REDACTED in output


@pytest.mark.parametrize(("label", "payload", "secrets"), CASES, ids=[c[0] for c in CASES])
def test_format_argument_redaction_matrix(
    label: str, payload: str, secrets: tuple[str, ...]
) -> None:
    output = _capture(lambda logger: logger.info("payload %s", payload))

    for secret in secrets:
        assert secret not in output, f"{label}: secret leaked through %-args"
    if secrets:
        assert REDACTED in output


@pytest.mark.parametrize(("label", "payload", "secrets"), CASES, ids=[c[0] for c in CASES])
def test_log_safe_field_redaction_matrix(
    label: str, payload: str, secrets: tuple[str, ...]
) -> None:
    output = _capture(lambda logger: log_safe(logger, logging.INFO, "event", {"detail": payload}))

    for secret in secrets:
        assert secret not in output, f"{label}: secret leaked through log_safe"
    if secrets:
        assert REDACTED in output


@pytest.mark.parametrize(("label", "payload", "secrets"), CASES, ids=[c[0] for c in CASES])
def test_exception_redaction_matrix(label: str, payload: str, secrets: tuple[str, ...]) -> None:
    def write(logger: logging.Logger) -> None:
        try:
            raise RuntimeError(payload)
        except RuntimeError:
            logger.exception("boom")

    output = _capture(write)

    assert "RuntimeError" in output
    for secret in secrets:
        assert secret not in output, f"{label}: secret leaked through a traceback"
    if secrets:
        assert REDACTED in output


def test_unrelated_payload_is_not_modified() -> None:
    payload = "professor_id=42 outcome=success"

    assert payload in _capture(lambda logger: logger.info(payload))
