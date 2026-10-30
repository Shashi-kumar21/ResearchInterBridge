from __future__ import annotations

import io
import logging

from research_inter_bridge.logging import (
    REDACTED,
    RedactingFormatter,
    RedactionFilter,
    configure_logging,
    get_logger,
    log_safe,
    redact,
    redact_mapping,
)


def test_redacts_sensitive_key_value_pairs() -> None:
    text = (
        "refresh_token=abc123 client_secret: s3cr3t "
        "Authorization=Bearer-value password='hunter2' user=alice"
    )

    redacted = redact(text)

    assert "abc123" not in redacted
    assert "s3cr3t" not in redacted
    assert "Bearer-value" not in redacted
    assert "hunter2" not in redacted
    assert "user=alice" in redacted
    assert redacted.count(REDACTED) == 4


def test_redacts_known_token_shapes_without_keys() -> None:
    text = (
        "visited ya29.a0AfH6SMBexampletoken "
        "refresh 1//0gExampleRefreshTokenValue "
        "github ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa "
        "openai sk-abcdefghijklmnopqrstuvwxyz012345"
    )

    redacted = redact(text)

    for secret in (
        "ya29.a0AfH6SMBexampletoken",
        "1//0gExampleRefreshTokenValue",
        "ghp_aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
        "sk-abcdefghijklmnopqrstuvwxyz012345",
    ):
        assert secret not in redacted
    assert redacted.count(REDACTED) == 4


def test_redact_leaves_ordinary_text_untouched() -> None:
    text = "professor_id=42 action=draft.created outcome=success"

    assert redact(text) == text
    assert redact("") == ""


def test_redact_mapping_masks_sensitive_keys_recursively() -> None:
    payload = {
        "action": "oauth.connect",
        "access_token": "ya29.secret",
        "credentials": {"refresh_token": "1//secret", "scope": "gmail.compose"},
        "notes": ["token=abc", "plain"],
        "attempts": 2,
        "ok": True,
        "missing": None,
    }

    cleaned = redact_mapping(payload)

    assert cleaned["access_token"] == REDACTED
    assert cleaned["credentials"] == REDACTED
    assert cleaned["notes"] == [f"token={REDACTED}", "plain"]
    assert cleaned["attempts"] == 2
    assert cleaned["ok"] is True
    assert cleaned["missing"] is None


def test_filter_redacts_formatted_record() -> None:
    record = logging.LogRecord(
        name="test",
        level=logging.INFO,
        pathname=__file__,
        lineno=1,
        msg="connect token=%s",
        args=("super-secret-value",),
        exc_info=None,
    )

    assert RedactionFilter().filter(record) is True
    assert "super-secret-value" not in record.getMessage()
    assert REDACTED in record.getMessage()


def test_configured_logger_never_emits_secrets() -> None:
    stream = io.StringIO()
    configure_logging("DEBUG", stream=stream)

    logger = get_logger("research_inter_bridge.tests")
    logger.info("token=%s", "top-secret-token")
    log_safe(logger, logging.INFO, "draft.created", {"draft_id": 7, "access_token": "abc"})

    output = stream.getvalue()
    assert "top-secret-token" not in output
    assert "access_token='abc'" not in output
    assert f"access_token={REDACTED}" in output
    assert "draft_id=7" in output


def test_configure_logging_replaces_previous_handlers() -> None:
    configure_logging("INFO", stream=io.StringIO())
    configure_logging("WARNING", stream=io.StringIO())

    root = logging.getLogger()
    assert len(root.handlers) == 1
    assert root.level == logging.WARNING
    handler = root.handlers[0]
    assert any(isinstance(f, RedactionFilter) for f in handler.filters)
    assert isinstance(handler.formatter, RedactingFormatter)


def test_exception_tracebacks_are_redacted() -> None:
    stream = io.StringIO()
    configure_logging("DEBUG", stream=stream)

    try:
        raise RuntimeError(
            "call failed for https://api.example.com/x?access_token=ya29.SECRETSECRET123"
        )
    except RuntimeError:
        get_logger("research_inter_bridge.tests").exception("request failed")

    output = stream.getvalue()
    assert "request failed" in output
    assert "RuntimeError" in output
    assert "SECRETSECRET123" not in output
    assert REDACTED in output


def test_exception_with_basic_auth_header_is_redacted() -> None:
    stream = io.StringIO()
    configure_logging("DEBUG", stream=stream)

    try:
        raise ValueError("Authorization: Basic dXNlcjpwYXNzd29yZA==")
    except ValueError:
        get_logger("research_inter_bridge.tests").exception("login failed")

    output = stream.getvalue()
    assert "dXNlcjpwYXNzd29yZA==" not in output
    assert REDACTED in output
