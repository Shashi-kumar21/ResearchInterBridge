"""Shared pytest fixtures.

``cli`` runs the real CLI entry point against a temporary database and data
directory, so tests exercise the same code path a user runs in PowerShell.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import pytest

from research_inter_bridge.cli import main
from research_inter_bridge.config import Settings

_ENV_KEYS = ("RIB_DATABASE_PATH", "RIB_DATA_DIRECTORY")


@dataclass(frozen=True)
class CliRunner:
    """Callable wrapper around ``main`` plus the settings it used."""

    settings: Settings
    run: Callable[..., tuple[int, str]]


@pytest.fixture
def cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> CliRunner:
    settings = Settings(database_path=tmp_path / "app.db", data_directory=tmp_path / "data")

    def run(*argv: str) -> tuple[int, str]:
        previous = {key: os.environ.get(key) for key in _ENV_KEYS}
        os.environ["RIB_DATABASE_PATH"] = str(settings.database_path)
        os.environ["RIB_DATA_DIRECTORY"] = str(settings.data_directory)
        try:
            code = main(["--env-file", str(tmp_path / "missing.env"), *argv])
            return code, capsys.readouterr().out
        finally:
            for key, value in previous.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    return CliRunner(settings=settings, run=run)
