"""CLI surface for Phase 2b: user creation and document ingestion."""

from __future__ import annotations

import io
import json
from pathlib import Path

import pymupdf
from docx import Document as WordDocument

from research_inter_bridge import db


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


def test_user_create_then_document_add_parse_and_list(cli, tmp_path: Path) -> None:
    code, out = cli.run("user-create")
    assert code == 0
    user_id = json.loads(out)["user_id"]

    code, out = cli.run(
        "document-add",
        "--user-id",
        str(user_id),
        "--file",
        str(_pdf(tmp_path / "cv.pdf", "Asha Verma asha@example.edu CGPA: 8.9/10")),
        "--parse",
    )
    assert code == 0
    payload = json.loads(out)
    assert payload["document"]["status"] == "parsed"
    assert payload["facts_extracted"] >= 2

    code, out = cli.run("document-list", "--user-id", str(user_id))
    assert code == 0
    listed = json.loads(out)["documents"]
    assert len(listed) == 1
    assert listed[0]["display_name"] == "cv.pdf"


def test_document_add_reports_rejected_files(cli, tmp_path: Path) -> None:
    _code, out = cli.run("user-create")
    user_id = json.loads(out)["user_id"]
    bad = tmp_path / "cv.pdf"
    bad.write_bytes(b"MZ\x90\x00 definitely not a pdf")

    code, _out = cli.run("document-add", "--user-id", str(user_id), "--file", str(bad))

    assert code == 1


def test_document_add_for_unknown_user_fails(cli, tmp_path: Path) -> None:
    code, _out = cli.run(
        "document-add",
        "--user-id",
        "999",
        "--file",
        str(_docx(tmp_path / "cv.docx", "Asha Verma")),
    )
    assert code == 1


def test_document_list_on_unmigrated_database_is_created_empty(cli) -> None:
    code, out = cli.run("document-list", "--user-id", "1")

    assert code == 0
    assert json.loads(out) == {"documents": []}
    with db.session(cli.settings.database_path) as connection:
        assert db.schema_version(connection) == db.latest_version()
