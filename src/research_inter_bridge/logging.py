"""Structured logging with mandatory redaction of credentials.

Rules enforced here (see task specification section 5):

* OAuth tokens, passwords, API keys, cookies and HTTP authorization values are
  replaced by ``[REDACTED]`` before a record reaches any handler **or any
  formatter**, including exception tracebacks.
* Message bodies, email contents and uploaded file contents must never be
  logged at ``INFO`` or below; callers pass identifiers instead. The
  :func:`log_safe` helper makes that intent explicit.

Redaction happens twice on purpose:

1. :class:`RedactionFilter` rewrites ``record.msg`` so other filters/handlers
   added in the future see the cleaned message (QA nit n1).
2. :class:`RedactingFormatter` runs :func:`redact` over the *fully formatted*
   output, which is the only place ``exc_info``/tracebacks become text (QA M1).
   Use it (or :func:`configure_logging`) for every handler.

Known limitation (tracked, not a regression): an *unquoted* multi-word secret
survives partially - ``api_key=two words`` masks ``two`` and leaves ``words``.
Secret values containing spaces must be logged quoted (``api_key='two words'``)
or as structured fields through :func:`log_safe`. Widening the bare-value rule
would let a match swallow the following ``key=`` on the same line, which is the
bug that made QA finding M2/M3 worse than no redaction.
"""

from __future__ import annotations

import logging
import re
import sys
from collections.abc import Mapping, MutableMapping
from typing import Any, TextIO

REDACTED = "[REDACTED]"

LOG_FORMAT = "%(asctime)s %(levelname)-8s %(name)s %(message)s"
DATE_FORMAT = "%Y-%m-%dT%H:%M:%S%z"

_SENSITIVE_KEY_RE = re.compile(
    r"(?i)(token|secret|password|passwd|api[-_]?key|auth|authorization|cookie|credential|session[-_]?id)"
)

# A key containing a sensitive word, optionally quoted (JSON / dict repr), with
# arbitrary prefix/suffix such as ``access_token`` or ``X-Api-Key``. Building the
# key pattern from the sensitive words means non-sensitive pairs are never
# matched, so a match can never swallow the next ``key=`` on the same line.
_SENSITIVE_KEY_PATTERN = (
    r"(?i:[\"']?(?:[A-Za-z0-9_.\-\[\]]*"
    r"(?:token|secret|password|passwd|api[-_]?key|auth|cookie|credential|session[-_]?id)"
    r"[A-Za-z0-9_.\-\[\]]*)[\"']?)"
)

# ``key=value`` / ``key: value`` / ``"key": "value"`` pairs. Values may be quoted
# or a bare token; an unquoted value stops at whitespace (see module docstring
# for the known limitation).
_KV_RE = re.compile(
    r"(?P<key>" + _SENSITIVE_KEY_PATTERN + r")"
    r"(?P<sep>\s*[=:]\s*)"
    r"(?P<value>\"(?:\\.|[^\"\\])*\"|'(?:\\.|[^'\\])*'|[^\s,;&}]+)"
)

# ``Authorization: Basic <base64>`` style values: the credential is the token
# after the scheme, so match the pair before the generic rule can redact only
# the scheme word and leave the secret behind (QA M2).
_AUTH_VALUE_RE = re.compile(
    r"(?P<key>" + _SENSITIVE_KEY_PATTERN + r")"
    r"(?P<sep>\s*[=:]\s*)"
    r"(?P<scheme>(?i:bearer|basic|digest|negotiate|token))"
    r"\s+(?P<credential>[^\s,;&}]+)"
)

# ``Basic <base64>`` / ``Digest <base64>`` logged without its header key. The
# lookahead requires a digit or base64 padding so ordinary prose such as
# "basic configuration" is left alone.
_AUTH_SCHEME_RE = re.compile(
    r"(?i)\b(?:basic|digest|negotiate)\s+(?=[A-Za-z0-9+/]*[0-9=])[A-Za-z0-9+/=]{12,}"
)

# Well-known credential shapes that may appear without a key.
_TOKEN_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)\bbearer\s+[A-Za-z0-9._\-~+/]{8,}=*"),
    _AUTH_SCHEME_RE,
    re.compile(r"\bya29\.[A-Za-z0-9._\-]{10,}"),  # Google OAuth access token
    re.compile(r"\b1//[A-Za-z0-9._\-]{10,}"),  # Google OAuth refresh token
    re.compile(r"\beyJ[A-Za-z0-9._\-]{10,}"),  # JWT (header fragment)
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{20,}"),  # GitHub tokens
    re.compile(r"\bsk-[A-Za-z0-9\-_]{16,}"),  # OpenAI-style API keys
)


def _replace_value(match: re.Match[str]) -> str:
    key = match.group("key")
    if not _SENSITIVE_KEY_RE.search(key):
        return match.group(0)
    value = match.group("value")
    return f"{key}{match.group('sep')}{REDACTED if value else value}"


def _replace_auth_value(match: re.Match[str]) -> str:
    key = match.group("key")
    if not _SENSITIVE_KEY_RE.search(key):
        return match.group(0)
    return f"{key}{match.group('sep')}{REDACTED}"


def redact(text: str) -> str:
    """Return ``text`` with credential-looking substrings masked."""

    if not text:
        return text
    redacted = _AUTH_VALUE_RE.sub(_replace_auth_value, text)
    redacted = _KV_RE.sub(_replace_value, redacted)
    for pattern in _TOKEN_PATTERNS:
        redacted = pattern.sub(REDACTED, redacted)
    return redacted


def redact_mapping(values: Mapping[str, Any]) -> dict[str, Any]:
    """Redact a mapping recursively, masking values whose key looks sensitive.

    Values are converted to strings so that a caller cannot leak an object's
    ``repr`` (which may contain secrets) into a log record or audit event.
    """

    cleaned: dict[str, Any] = {}
    for key, value in values.items():
        if _SENSITIVE_KEY_RE.search(str(key)):
            cleaned[str(key)] = REDACTED
        elif isinstance(value, Mapping):
            cleaned[str(key)] = redact_mapping(value)
        elif isinstance(value, (list, tuple)):
            cleaned[str(key)] = [
                redact_mapping(item) if isinstance(item, Mapping) else redact(str(item))
                for item in value
            ]
        elif value is None or isinstance(value, (bool, int, float)):
            cleaned[str(key)] = value
        else:
            cleaned[str(key)] = redact(str(value))
    return cleaned


class RedactionFilter(logging.Filter):
    """Logging filter that redacts credentials from the formatted message."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - defensive against bad format args
            message = str(record.msg)
        record.msg = redact(message)
        record.args = ()
        return True


class RedactingFormatter(logging.Formatter):
    """Formatter whose final output (message, traceback, stack) is redacted.

    ``Formatter.format`` appends ``formatException`` output, so this is the only
    reliable place to catch credentials inside tracebacks.
    """

    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def configure_logging(level: str = "INFO", stream: TextIO | None = None) -> None:
    """Configure the root logger once, with redaction attached to every handler.

    Redaction cannot be disabled: both the filter and the redacting formatter are
    installed unconditionally.
    """

    root = logging.getLogger()
    for handler in list(root.handlers):
        root.removeHandler(handler)
        handler.close()

    handler = logging.StreamHandler(stream or sys.stderr)
    handler.setFormatter(RedactingFormatter(LOG_FORMAT, DATE_FORMAT))
    handler.addFilter(RedactionFilter())
    root.addHandler(handler)
    root.setLevel(level.upper())


def get_logger(name: str) -> logging.Logger:
    """Return a module logger; redaction is guaranteed by :func:`configure_logging`."""

    return logging.getLogger(name)


def log_safe(
    logger: logging.Logger,
    level: int,
    event: str,
    fields: Mapping[str, Any] | None = None,
) -> None:
    """Emit ``event`` with redacted ``fields`` and no message body.

    Pass identifiers and status values only. Never pass email bodies, document
    text or tokens, even though redaction runs as a safety net.
    """

    payload: MutableMapping[str, Any] = {"event": event}
    if fields:
        payload.update(redact_mapping(fields))
    details = " ".join(f"{key}={value!r}" for key, value in payload.items())
    logger.log(level, details)
