"""``serve`` command: the CLI hands the real app and settings to uvicorn.

The server itself is never started here; uvicorn's blocking ``run`` is replaced
with a recorder so the test can assert what would have been served, from where,
without binding a port.
"""

from __future__ import annotations

from typing import Any

import pytest
import uvicorn


@pytest.fixture
def captured_run(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    calls: dict[str, Any] = {}

    def fake_run(app: Any, *, host: str, port: int, log_level: str, **_kwargs: Any) -> None:
        calls.update(app=app, host=host, port=port, log_level=log_level)

    monkeypatch.setattr(uvicorn, "run", fake_run)
    return calls


def test_serve_defaults_to_loopback(cli, captured_run: dict[str, Any]) -> None:
    code, output = cli.run("serve")

    assert code == 0
    assert output == ""
    assert captured_run["host"] == "127.0.0.1"
    assert captured_run["port"] == 8000
    assert captured_run["log_level"] == "info"


def test_serve_uses_the_settings_the_cli_loaded(cli, captured_run: dict[str, Any]) -> None:
    code, _ = cli.run("serve", "--host", "0.0.0.0", "--port", "8123")

    assert code == 0
    assert captured_run["host"] == "0.0.0.0"
    assert captured_run["port"] == 8123
    app = captured_run["app"]
    assert app.state.settings.database_path == cli.settings.database_path
    assert app.state.settings.data_directory == cli.settings.data_directory
