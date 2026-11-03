from __future__ import annotations

from pathlib import Path

import pytest

from research_inter_bridge.config import (
    DEFAULT_DATABASE_PATH,
    DEFAULT_LOG_LEVEL,
    Settings,
    load_settings,
    parse_env_file,
)
from research_inter_bridge.errors import ConfigError


def test_defaults_are_local_and_secret_free() -> None:
    settings = Settings()

    assert settings.database_path == DEFAULT_DATABASE_PATH
    assert settings.log_level == DEFAULT_LOG_LEVEL
    assert settings.redact_logs is True
    assert settings.request_timeout_seconds > 0


def test_from_env_reads_prefixed_values_only() -> None:
    settings = Settings.from_env(
        {
            "RIB_DATABASE_PATH": "tmp/custom.db",
            "RIB_LOG_LEVEL": "debug",
            "RIB_REQUEST_TIMEOUT_SECONDS": "2.5",
            "RIB_DATA_DIRECTORY": "tmp",
            "DATABASE_PATH": "ignored.db",
        }
    )

    assert settings.database_path == Path("tmp/custom.db")
    assert settings.log_level == "DEBUG"
    assert settings.request_timeout_seconds == 2.5
    assert settings.data_directory == Path("tmp")


def test_blank_values_fall_back_to_defaults() -> None:
    settings = Settings.from_env({"RIB_DATABASE_PATH": "   ", "RIB_LOG_LEVEL": ""})

    assert settings.database_path == DEFAULT_DATABASE_PATH
    assert settings.log_level == DEFAULT_LOG_LEVEL


@pytest.mark.parametrize("value", ["verbose", "WARN", "10"])
def test_invalid_log_level_names_the_setting_and_allowed_values(value: str) -> None:
    with pytest.raises(ConfigError) as excinfo:
        Settings.from_env({"RIB_LOG_LEVEL": value})

    message = str(excinfo.value)
    assert "RIB_LOG_LEVEL" in message
    assert "DEBUG" in message


def test_redaction_cannot_be_disabled_by_environment() -> None:
    settings = Settings.from_env({"RIB_REDACT_LOGS": "false", "RIB_REDACT_LOGS ": "0"})

    assert settings.redact_logs is True
    with pytest.raises(TypeError):
        Settings(redact_logs=False)  # type: ignore[call-arg]


def test_invalid_timeout_is_rejected() -> None:
    with pytest.raises(ConfigError, match="RIB_REQUEST_TIMEOUT_SECONDS"):
        Settings.from_env({"RIB_REQUEST_TIMEOUT_SECONDS": "soon"})

    with pytest.raises(ConfigError, match="greater than 0"):
        Settings.from_env({"RIB_REQUEST_TIMEOUT_SECONDS": "0"})


def test_resolved_database_path_is_absolute(tmp_path: Path) -> None:
    settings = Settings(database_path=tmp_path / "nested" / "app.db")

    resolved = settings.resolved_database_path
    assert resolved.is_absolute()
    assert resolved.name == "app.db"


def test_parse_env_file_supports_comments_quotes_and_export(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\n"
        "\n"
        "RIB_LOG_LEVEL=warning\n"
        'export RIB_DATABASE_PATH="data/quoted.db"\n'
        "RIB_DATA_DIRECTORY='data dir'\n",
        encoding="utf-8",
    )

    values = parse_env_file(env_file)

    assert values == {
        "RIB_LOG_LEVEL": "warning",
        "RIB_DATABASE_PATH": "data/quoted.db",
        "RIB_DATA_DIRECTORY": "data dir",
    }


def test_parse_env_file_rejects_malformed_lines(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("RIB_LOG_LEVEL\n", encoding="utf-8")

    with pytest.raises(ConfigError, match=".env:1"):
        parse_env_file(env_file)


def test_parse_env_file_reports_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match=".env.example"):
        parse_env_file(tmp_path / "absent.env")


def test_real_environment_wins_over_env_file(tmp_path: Path) -> None:
    env_file = tmp_path / ".env"
    env_file.write_text("RIB_LOG_LEVEL=warning\nRIB_DATA_DIRECTORY=from-file\n", encoding="utf-8")

    settings = load_settings(env={"RIB_LOG_LEVEL": "error"}, env_file=env_file)

    assert settings.log_level == "ERROR"
    assert settings.data_directory == Path("from-file")


def test_missing_env_file_is_not_an_error(tmp_path: Path) -> None:
    settings = load_settings(env={}, env_file=tmp_path / "absent.env")

    assert settings.log_level == DEFAULT_LOG_LEVEL


def test_settings_are_immutable_and_overridable() -> None:
    original = Settings()
    changed = original.with_overrides(log_level="DEBUG")

    assert original.log_level == DEFAULT_LOG_LEVEL
    assert changed.log_level == "DEBUG"

    with pytest.raises(AttributeError):
        original.log_level = "ERROR"  # type: ignore[misc]


def test_cors_origins_are_trimmed_deduplicated_and_trailing_slash_free() -> None:
    settings = Settings.from_env(
        {
            "RIB_CORS_ORIGINS": (
                " http://localhost:3000 , https://app.example.edu/ , http://localhost:3000 "
            )
        }
    )

    assert settings.cors_origins == ("http://localhost:3000", "https://app.example.edu")


def test_cors_origins_default_to_none_trusted() -> None:
    assert Settings().cors_origins == ()
    assert Settings.from_env({}).cors_origins == ()


@pytest.mark.parametrize("value", ["*", "https://*.example.edu", "http://*:3000"])
def test_cors_origins_reject_wildcards(value: str) -> None:
    with pytest.raises(ConfigError, match="wildcards are not allowed"):
        Settings.from_env({"RIB_CORS_ORIGINS": value})


@pytest.mark.parametrize(
    "value",
    [
        "app.example.edu",  # no scheme
        "https://app.example.edu/api",  # path
        "https://user:secret@app.example.edu",  # credentials
        "https://app.example.edu?debug=1",  # query
        "https://app.example.edu#top",  # fragment
        "ftp://app.example.edu",  # not a browser origin
    ],
)
def test_cors_origins_require_scheme_host_and_optional_port(value: str) -> None:
    with pytest.raises(ConfigError, match="RIB_CORS_ORIGINS"):
        Settings.from_env({"RIB_CORS_ORIGINS": value})


def test_settings_validate_cors_origins_on_direct_construction() -> None:
    with pytest.raises(ConfigError, match="wildcards"):
        Settings(cors_origins=("*",))


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("1", True),
        ("true", True),
        ("TRUE", True),
        ("yes", True),
        ("on", True),
        ("0", False),
        ("false", False),
        ("No", False),
        ("off", False),
    ],
)
def test_cookie_secure_accepts_documented_boolean_spellings(value: str, expected: bool) -> None:
    assert Settings.from_env({"RIB_COOKIE_SECURE": value}).cookie_secure is expected


def test_cookie_secure_defaults_to_false_for_local_http() -> None:
    assert Settings().cookie_secure is False


def test_invalid_cookie_secure_names_the_setting() -> None:
    with pytest.raises(ConfigError, match="RIB_COOKIE_SECURE"):
        Settings.from_env({"RIB_COOKIE_SECURE": "sometimes"})


def test_session_cookie_name_defaults_to_rib_session() -> None:
    assert Settings().session_cookie_name == "rib_session"


@pytest.mark.parametrize("value", ["rib session", "rib;session", "rib=session", "", "ri\nb"])
def test_session_cookie_name_must_be_a_cookie_token(value: str) -> None:
    # An empty value falls back to the default, so it is not an error; the rest
    # must be refused before Starlette can build a malformed header.
    if not value.strip():
        assert Settings.from_env({"RIB_SESSION_COOKIE_NAME": value}).session_cookie_name == (
            "rib_session"
        )
        return
    with pytest.raises(ConfigError, match="RIB_SESSION_COOKIE_NAME"):
        Settings.from_env({"RIB_SESSION_COOKIE_NAME": value})


def test_cors_origins_are_normalised_to_lowercase() -> None:
    settings = Settings.from_env(
        {"RIB_CORS_ORIGINS": "HTTPS://App.Example.EDU, http://LOCALHOST:3000"}
    )

    assert settings.cors_origins == ("https://app.example.edu", "http://localhost:3000")


def test_cors_origins_that_differ_only_in_case_are_one_entry() -> None:
    settings = Settings(cors_origins=("https://App.Example.EDU", "https://app.example.edu"))

    assert settings.cors_origins == ("https://app.example.edu",)
