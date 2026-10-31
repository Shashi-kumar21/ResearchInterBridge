"""CLI surface for Phase 2b: user creation and document ingestion."""

from __future__ import annotations

import io
import json
import sqlite3
from pathlib import Path

import pymupdf
from docx import Document as WordDocument

from research_inter_bridge import db
from research_inter_bridge.cli import main
from research_inter_bridge.config import Settings


def _pdf(path: Path, text: str) -> Path:
    document = pymupdf.open()
    document.new_page().insert_text((72, 72), text, fontsize=11)
    path.write_bytes(document.tobytes())
    document.close()
    return path


def _docx(path: Path, *paragraphs: str) -> Path:
    document = WordDocument()
    for paragraph in paragraphs:
        document.add_paragraph(paragraph)
    buffer = io.BytesIO()
    document.save(buffer)
    path.write_bytes(buffer.getvalue())
    return path


def _settings(tmp_path: Path) -> Settings:
    return Settings(database_path=tmp_path / "app.db", data_directory=tmp_path / "data")


def _run(settings: Settings, *argv: str, capsys) -> tuple[int, str]:
    # Point the CLI at the test database through the environment, because
    # load_settings() reads real environment variables.
    import os

    previous = os.environ.get("RIB_DATABASE_PATH"), os.environ.get("RIB_DATA_DIRECTORY")
    os.environ["RIB_DATABASE_PATH"] = str(settings.database_path)
    os.environ["RIB_DATA_DIRECTORY"] = str(settings.data_directory)
    try:
        code = main(["--env-file", str(settings.database_path.parent / "missing.env"), *argv])
        return code, capsys.readouterr().out
    finally:
        for key, value in zip(("RIB_DATABASE_PATH", "RIB_DATA_DIRECTORY"), previous, strict=True):
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


def test_user_create_then_document_add_parse_and_list(tmp_path: Path, capsys) -> None:
    settings = _settings(tmp_path)
    code, out = _run(settings, "user-create", capsys=capsys)
    assert code == 0
    user_id = json.loads(out)["user_id"]

    code, out = _run(
        settings,
        "document-add",
        "--user-id",
        str(user_id),
        "--file",
        str(_pdf(tmp_path / "cv.pdf", "Asha Verma asha@example.edu CGPA: 8.9/10")),
        "--parse",
        capsys=capsys,
    )
    assert code == 0
    payload = json.loads(out)
    assert payload["document"]["status"] == "parsed"
    assert payload["facts_extracted"] >= 2

    code, out = _run(settings, "document-list", "--user-id", str(user_id), capsys=capsys)
    assert code == 0
    listed = json.loads(out)["documents"]
    assert len(listed) == 1
    assert listed[0]["display_name"] == "cv.pdf"


def test_document_add_reports_rejected_files(tmp_path: Path, capsys) -> None:
    settings = _settings(tmp_path)
    _code, out = _run(settings, "user-create", capsys=capsys)
    user_id = json.loads(out)["user_id"]
    bad = tmp_path / "cv.pdf"
    bad.write_bytes(b"MZ\x90\x00 definitely not a pdf")

    code, _out = _run(
        settings,
        "document-add",
        "--user-id",
        str(user_id),
        "--file",
        str(bad),
        capsys=capsys,
    )

    assert code == 1


def test_document_add_for_unknown_user_fails(tmp_path: Path, capsys) -> None:
    settings = _settings(tmp_path)
    code, _out = _run(
        settings,
        "document-add",
        "--user-id",
        "999",
        "--file",
        str(_docx(tmp_path / "cv.docx", "Asha Verma")),
        capsys=capsys,
    )
    assert code == 1


def test_document_list_on_unmigrated_database_is_created_empty(tmp_path: Path, capsys) -> None:
    settings = _settings(tmp_path)
    code, out = _run(settings, "document-list", "--user-id", "1", capsys=capsys)

    assert code == 0
    assert json.loads(out) == {"documents": []}
    with db.session(settings.database_path) as connection:
        assert db.schema_version(connection) == db.latest_version()
    assert isinstance(settings.database_path, Path)
    assert sqlite3.sqlite_version
