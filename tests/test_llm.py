"""The optional LLM layer (task.md §6, ADR-025): off by default, safe when on."""

from __future__ import annotations

import json
import logging
import socket
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from research_inter_bridge import db, llm, profiles, ratelimit
from research_inter_bridge.auth import export_account_data, register
from research_inter_bridge.config import Settings, load_settings
from research_inter_bridge.errors import ConfigError, LlmError

NOW = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)
LINE = "Worked on data pipelines for 2 labs"
TERMS = ("deep learning", "proteins")
SECRET = "sk-test-very-secret-value"


@pytest.fixture
def connection(tmp_path: Path):
    with db.session(tmp_path / "app.db") as conn:
        db.apply_migrations(conn)
        yield conn


@pytest.fixture
def user(connection: sqlite3.Connection) -> int:
    return register(
        connection,
        email="asha@example.edu",
        password="correct horse battery staple",
        consents=("personal_data", "document_processing"),
    ).user_id


def settings(tmp_path: Path, **overrides: object) -> Settings:
    values: dict[str, object] = {
        "database_path": tmp_path / "app.db",
        "data_directory": tmp_path / "data",
        "llm_provider": "mock",
        "llm_api_key": SECRET,
    }
    values.update(overrides)
    return Settings(**values)  # type: ignore[arg-type]


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("the LLM layer must make no outbound call in tests")

    monkeypatch.setattr(socket.socket, "connect", refuse)


# --------------------------------------------------------------------------- #
# off by default and secrets
# --------------------------------------------------------------------------- #


def test_the_layer_is_off_by_default(
    connection: sqlite3.Connection, user: int, tmp_path: Path
) -> None:
    default = Settings(database_path=tmp_path / "app.db")

    assert default.llm_provider == "off"
    assert llm.provider_from_settings(default) is None
    with pytest.raises(LlmError) as excinfo:
        llm.rewrite_line(connection, user_id=user, line=LINE, target_terms=TERMS, settings=default)
    assert excinfo.value.code == "llm_disabled"
    assert connection.execute("SELECT COUNT(*) FROM llm_usage").fetchone()[0] == 0


def test_only_implemented_providers_are_accepted() -> None:
    with pytest.raises(ConfigError, match="Real providers are added after the Owner picks one"):
        load_settings({"RIB_LLM_PROVIDER": "anthropic"})
    assert load_settings({"RIB_LLM_PROVIDER": "MOCK"}).llm_provider == "mock"


def test_the_api_key_never_appears_in_repr_or_logs(
    connection: sqlite3.Connection, user: int, tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    loaded = load_settings({"RIB_LLM_PROVIDER": "mock", "RIB_LLM_API_KEY": SECRET})
    caplog.set_level(logging.DEBUG)

    # A failing call exercises the warning log path with the key loaded.
    with pytest.raises(LlmError):
        llm.rewrite_line(
            connection,
            user_id=user,
            line=LINE,
            target_terms=TERMS,
            settings=loaded,
            provider=llm.MockProvider(delay_seconds=99),
        )

    assert loaded.llm_api_key == SECRET
    assert SECRET not in repr(loaded)
    assert SECRET not in caplog.text


# --------------------------------------------------------------------------- #
# prompts and output guard
# --------------------------------------------------------------------------- #


def test_prompts_carry_only_the_line_and_terms_without_contact_details() -> None:
    prompt = llm.build_rewrite_prompt(
        "Built tools, see https://asha.dev or mail asha@example.edu, +49 151 2345 6789", TERMS
    )

    assert "asha@example.edu" not in prompt
    assert "https://asha.dev" not in prompt
    assert "2345" not in prompt
    assert "deep learning, proteins" in prompt
    assert prompt.count("\n") == 1


def test_a_rewrite_that_invents_a_number_is_rejected(
    connection: sqlite3.Connection, user: int, tmp_path: Path
) -> None:
    provider = llm.MockProvider(respond=lambda request: "Built data pipelines for 5 labs")

    with pytest.raises(LlmError) as excinfo:
        llm.rewrite_line(
            connection,
            user_id=user,
            line=LINE,
            target_terms=TERMS,
            settings=settings(tmp_path),
            provider=provider,
            now=NOW,
        )

    assert excinfo.value.code == "llm_output_rejected"
    # The rejected output is never cached, but the spent tokens are recorded.
    assert connection.execute("SELECT COUNT(*) FROM llm_cache").fetchone()[0] == 0
    assert connection.execute("SELECT COUNT(*) FROM llm_usage").fetchone()[0] == 1


@pytest.mark.parametrize(
    "output",
    ["", "   ", "Built pipelines; contact me at asha@example.edu", "x" * 400],
)
def test_malformed_or_contact_outputs_are_rejected(output: str) -> None:
    with pytest.raises(LlmError) as excinfo:
        llm.vet_rewrite(LINE, output)
    assert excinfo.value.code == "llm_output_rejected"


def test_an_accepted_rewrite_is_labelled_and_reuses_the_source_numbers(
    connection: sqlite3.Connection, user: int, tmp_path: Path
) -> None:
    result = llm.rewrite_line(
        connection,
        user_id=user,
        line=LINE,
        target_terms=TERMS,
        settings=settings(tmp_path),
        provider=llm.MockProvider(respond=lambda request: "Built data pipelines for 2 labs"),
        now=NOW,
    )

    assert result.text == "Built data pipelines for 2 labs"
    assert result.ai_generated is True
    assert result.cached is False


# --------------------------------------------------------------------------- #
# cost controls
# --------------------------------------------------------------------------- #


def test_identical_inputs_are_served_from_the_cache(
    connection: sqlite3.Connection, user: int, tmp_path: Path
) -> None:
    provider = llm.MockProvider(respond=lambda request: "Built data pipelines for 2 labs")
    config = settings(tmp_path)

    first = llm.rewrite_line(
        connection,
        user_id=user,
        line=LINE,
        target_terms=TERMS,
        settings=config,
        provider=provider,
        now=NOW,
    )
    again = llm.rewrite_line(
        connection,
        user_id=user,
        line=LINE,
        target_terms=TERMS,
        settings=config,
        provider=provider,
        now=NOW,
    )

    assert (first.cached, again.cached) == (False, True)
    assert provider.calls == 1
    assert connection.execute("SELECT COUNT(*) FROM llm_usage").fetchone()[0] == 1


def test_the_per_user_and_total_daily_caps_stop_calls(
    connection: sqlite3.Connection, user: int, tmp_path: Path
) -> None:
    provider = llm.MockProvider(respond=lambda request: "Built data pipelines for 2 labs")

    with pytest.raises(LlmError) as per_user:
        llm.rewrite_line(
            connection,
            user_id=user,
            line=LINE,
            target_terms=TERMS,
            settings=settings(tmp_path, llm_daily_tokens_per_user=10),
            provider=provider,
            now=NOW,
        )
    with pytest.raises(LlmError) as total:
        llm.rewrite_line(
            connection,
            user_id=user,
            line=LINE,
            target_terms=TERMS,
            settings=settings(tmp_path, llm_daily_tokens_total=10),
            provider=provider,
            now=NOW,
        )

    assert per_user.value.code == total.value.code == "llm_quota_exceeded"
    assert provider.calls == 0


def test_calls_are_rate_limited_per_account(
    connection: sqlite3.Connection, user: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from research_inter_bridge.errors import RateLimitError

    monkeypatch.setattr(
        ratelimit,
        "LLM_RULE",
        ratelimit.RateLimitRule(name="llm.call", limit=1, window_seconds=3600),
    )
    provider = llm.MockProvider(respond=lambda request: "Built data pipelines for 2 labs")
    config = settings(tmp_path)
    llm.rewrite_line(
        connection,
        user_id=user,
        line=LINE,
        target_terms=TERMS,
        settings=config,
        provider=provider,
        now=NOW,
    )

    with pytest.raises(RateLimitError):
        llm.rewrite_line(
            connection,
            user_id=user,
            line="Helped with experiments",
            target_terms=TERMS,
            settings=config,
            provider=provider,
            now=NOW,
        )


def test_a_slow_provider_times_out_with_a_stable_code(
    connection: sqlite3.Connection, user: int, tmp_path: Path
) -> None:
    with pytest.raises(LlmError) as excinfo:
        llm.rewrite_line(
            connection,
            user_id=user,
            line=LINE,
            target_terms=TERMS,
            settings=settings(tmp_path, llm_timeout_seconds=1),
            provider=llm.MockProvider(delay_seconds=5),
            now=NOW,
        )

    assert excinfo.value.code == "llm_unavailable"
    assert connection.execute("SELECT COUNT(*) FROM llm_usage").fetchone()[0] == 0


# --------------------------------------------------------------------------- #
# privacy plumbing
# --------------------------------------------------------------------------- #


def test_usage_and_cache_are_exported_audited_without_text_and_deleted_with_the_account(
    connection: sqlite3.Connection, user: int, tmp_path: Path
) -> None:
    llm.rewrite_line(
        connection,
        user_id=user,
        line=LINE,
        target_terms=TERMS,
        settings=settings(tmp_path),
        provider=llm.MockProvider(respond=lambda request: "Built data pipelines for 2 labs"),
        now=NOW,
    )

    exported = export_account_data(connection, user)["llm"]
    assert exported["cached_outputs"][0]["output"] == "Built data pipelines for 2 labs"
    assert exported["usage"][0]["purpose"] == "cv_rewrite"
    audit = connection.execute(
        "SELECT metadata_json FROM audit_events WHERE action = 'llm.call'"
    ).fetchone()
    assert set(json.loads(audit["metadata_json"])) == {
        "purpose",
        "prompt_tokens",
        "completion_tokens",
    }
    assert "pipelines" not in audit["metadata_json"]

    profiles.delete_user(connection, user, actor="test")
    assert connection.execute("SELECT COUNT(*) FROM llm_usage").fetchone()[0] == 0
    assert connection.execute("SELECT COUNT(*) FROM llm_cache").fetchone()[0] == 0
