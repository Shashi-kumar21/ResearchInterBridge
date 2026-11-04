"""Application settings loaded from environment variables.

Design rules:

* No secret ever lives in source code; values come from the environment.
* Unknown or invalid values fail loudly with an actionable :class:`ConfigError`
  instead of silently falling back to a default.
* ``.env`` files are parsed by this module (no third-party dependency) and never
  override variables that are already present in the real environment.
"""

from __future__ import annotations

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from urllib.parse import urlsplit

from .errors import ConfigError

ENV_PREFIX = "RIB_"

LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")

DEFAULT_DATABASE_PATH = Path("data") / "research_inter_bridge.db"
DEFAULT_LOG_LEVEL = "INFO"
DEFAULT_SESSION_COOKIE_NAME = "rib_session"

#: Cookie names are restricted to RFC 6265 token characters.
_COOKIE_NAME_RE = re.compile(r"^[!#$%&'*+\-.^_`|~0-9A-Za-z]+$")


def _setting_name(key: str) -> str:
    return f"{ENV_PREFIX}{key.upper()}"


@dataclass(frozen=True, slots=True)
class Settings:
    """Validated runtime settings.

    Attributes:
        database_path: SQLite file used for all persistent state.
        log_level: One of :data:`LOG_LEVELS`.
        redact_logs: Always ``True``. Redaction is a safety invariant, not an
            option: there is deliberately no environment variable for it.
        request_timeout_seconds: Default timeout for outbound HTTP adapters.
        data_directory: Directory that must stay outside version control.
        cors_origins: Exact browser origins allowed to call the JSON API. Empty
            (the default) means no cross-origin caller is trusted, which is the
            safe state for a self-hosted instance.
        cookie_secure: Send the session cookie with the ``Secure`` flag. Must be
            enabled whenever the API is served over HTTPS.
        session_cookie_name: Name of the HttpOnly session cookie.
    """

    database_path: Path = DEFAULT_DATABASE_PATH
    log_level: str = DEFAULT_LOG_LEVEL
    redact_logs: bool = field(default=True, init=False)
    request_timeout_seconds: float = 15.0
    data_directory: Path = Path("data")
    cors_origins: tuple[str, ...] = ()
    cookie_secure: bool = False
    session_cookie_name: str = DEFAULT_SESSION_COOKIE_NAME

    def __post_init__(self) -> None:
        if self.log_level not in LOG_LEVELS:
            raise ConfigError(
                f"{_setting_name('log_level')} must be one of {', '.join(LOG_LEVELS)}; "
                f"got {self.log_level!r}."
            )
        if self.request_timeout_seconds <= 0:
            raise ConfigError(
                f"{_setting_name('request_timeout_seconds')} must be greater than 0; "
                f"got {self.request_timeout_seconds!r}."
            )
        for origin in self.cors_origins:
            _validate_origin(origin)
        if not _COOKIE_NAME_RE.match(self.session_cookie_name):
            raise ConfigError(
                f"{_setting_name('session_cookie_name')} must be a cookie token "
                f"(letters, digits and !#$%&'*+-.^_`|~); got {self.session_cookie_name!r}."
            )
        # Scheme and host are case-insensitive (RFC 3986 §3.2.2, RFC 7230 §2.7),
        # but a browser sends the origin in lowercase. Normalising here means the
        # guard compares like with like instead of rejecting a valid request just
        # because the operator typed `HTTPS://App.Example.EDU`.
        object.__setattr__(
            self,
            "cors_origins",
            tuple(dict.fromkeys(origin.lower() for origin in self.cors_origins)),
        )

    @property
    def resolved_database_path(self) -> Path:
        """Absolute database path, resolved against the current directory."""

        return Path(self.database_path).expanduser().resolve()

    def with_overrides(self, **overrides: object) -> Settings:
        """Return a copy with the given fields replaced (validated on creation)."""

        return replace(self, **overrides)  # type: ignore[arg-type]

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> Settings:
        """Build settings from a mapping, defaulting to ``os.environ``."""

        source = os.environ if env is None else env
        return cls(
            database_path=Path(_read_text(source, "DATABASE_PATH", str(DEFAULT_DATABASE_PATH))),
            log_level=_read_text(source, "LOG_LEVEL", DEFAULT_LOG_LEVEL).upper(),
            request_timeout_seconds=_read_float(source, "REQUEST_TIMEOUT_SECONDS", 15.0),
            data_directory=Path(_read_text(source, "DATA_DIRECTORY", "data")),
            cors_origins=_read_list(source, "CORS_ORIGINS"),
            cookie_secure=_read_bool(source, "COOKIE_SECURE", False),
            session_cookie_name=_read_text(
                source, "SESSION_COOKIE_NAME", DEFAULT_SESSION_COOKIE_NAME
            ),
        )


def _read_raw(env: Mapping[str, str], key: str) -> str | None:
    value = env.get(_setting_name(key))
    if value is None:
        return None
    stripped = value.strip()
    return stripped or None


def _read_text(env: Mapping[str, str], key: str, default: str) -> str:
    value = _read_raw(env, key)
    return default if value is None else value


def _read_float(env: Mapping[str, str], key: str, default: float) -> float:
    value = _read_raw(env, key)
    if value is None:
        return default
    try:
        return float(value)
    except ValueError as exc:
        raise ConfigError(f"{_setting_name(key)} must be a number; got {value!r}.") from exc


def _read_bool(env: Mapping[str, str], key: str, default: bool) -> bool:
    value = _read_raw(env, key)
    if value is None:
        return default
    normalised = value.lower()
    if normalised in {"1", "true", "yes", "on"}:
        return True
    if normalised in {"0", "false", "no", "off"}:
        return False
    raise ConfigError(
        f"{_setting_name(key)} must be one of true/false, 1/0, yes/no, on/off; got {value!r}."
    )


def _read_list(env: Mapping[str, str], key: str) -> tuple[str, ...]:
    """Read a comma-separated list, dropping blanks and duplicates."""

    value = _read_raw(env, key)
    if value is None:
        return ()
    seen: dict[str, None] = {}
    for item in value.split(","):
        stripped = item.strip().rstrip("/")
        if stripped:
            seen.setdefault(stripped, None)
    return tuple(seen)


def _validate_origin(origin: str) -> None:
    """Reject origins a browser would never send and wildcards we never mean."""

    if origin == "*" or "*" in origin:
        raise ConfigError(
            f"{_setting_name('cors_origins')} must list exact origins; "
            f"wildcards are not allowed (got {origin!r})."
        )
    parts = urlsplit(origin)
    if parts.scheme not in {"http", "https"} or not parts.netloc:
        raise ConfigError(
            f"{_setting_name('cors_origins')} entries must look like "
            f"https://app.example.edu (got {origin!r})."
        )
    if parts.path or parts.query or parts.fragment or parts.username or parts.password:
        raise ConfigError(
            f"{_setting_name('cors_origins')} entries must be scheme + host + optional "
            f"port only, with no path, credentials or query (got {origin!r})."
        )


def parse_env_file(path: Path) -> dict[str, str]:
    """Parse a ``.env`` file into a mapping.

    Supports ``KEY=VALUE`` lines, ``#`` comments, blank lines, optional
    ``export`` prefixes and optional single/double quotes around the value.
    Malformed lines raise :class:`ConfigError` naming the offending line number.
    """

    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError as exc:
        raise ConfigError(
            f"Environment file {path} does not exist. Create it from .env.example."
        ) from exc
    except OSError as exc:
        raise ConfigError(f"Environment file {path} could not be read: {exc}") from exc

    values: dict[str, str] = {}
    for lineno, raw_line in enumerate(text.splitlines(), start=1):
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            raise ConfigError(f"{path}:{lineno} is not a KEY=VALUE line: {raw_line!r}.")
        key, _, value = line.partition("=")
        key = key.strip()
        if not key:
            raise ConfigError(f"{path}:{lineno} has an empty key.")
        values[key] = _unquote(value.strip())
    return values


def _unquote(value: str) -> str:
    if len(value) >= 2 and value[0] == value[-1] and value[0] in {"'", '"'}:
        return value[1:-1]
    return value


def load_settings(env: Mapping[str, str] | None = None, env_file: Path | None = None) -> Settings:
    """Load settings from the environment, optionally seeded by a ``.env`` file.

    Real environment variables always win over the file so that CI and shells
    can override a developer's local ``.env`` without editing it.
    """

    merged: dict[str, str] = {}
    if env_file is not None and Path(env_file).exists():
        merged.update(parse_env_file(Path(env_file)))
    merged.update(os.environ if env is None else env)
    return Settings.from_env(merged)
