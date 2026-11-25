from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from research_inter_bridge import consent, cv, db, documents, profiles
from research_inter_bridge.auth import export_account_data, register
from research_inter_bridge.errors import CvError, CvVersionNotFoundError

CV_TEXT = """Asha Rao
asha@example.edu

EDUCATION
MSc Computer Science, Example University, 2026

Research Experience
- Built a graph neural network for protein folding
  that improved accuracy by 4 points.
- Wrote data pipelines in Python.

Projects:
* Compiler for a toy language
* Robot arm controller

Skills
Python, PyTorch, C++

Relevant Coursework
Machine Learning; Bioinformatics
"""


@pytest.fixture
def connection(tmp_path: Path):
    with db.session(tmp_path / "app.db") as conn:
        db.apply_migrations(conn)
        yield conn


def account(connection: sqlite3.Connection, email: str = "asha@example.edu") -> int:
    user = register(
        connection,
        email=email,
        password="correct horse battery staple",
        consents=("personal_data", "document_processing"),
    )
    consent.record_consent(connection, user_id=user.user_id, kind="document_processing")
    return user.user_id


def upload(
    connection: sqlite3.Connection,
    user_id: int,
    data_dir: Path,
    *,
    text: str = CV_TEXT,
    kind: str = "cv",
    name: str = "cv.txt",
) -> int:
    record = documents.save_document(
        connection,
        user_id=user_id,
        display_name=name,
        payload=text.encode("utf-8"),
        data_directory=data_dir,
        kind=kind,
        actor=f"user:{user_id}",
    )
    return record.id


# --------------------------------------------------------------------------- #
# splitting
# --------------------------------------------------------------------------- #


def test_split_groups_lines_under_headings_without_changing_them() -> None:
    sections = cv.split_sections(CV_TEXT)

    assert [(s.kind, s.heading) for s in sections] == [
        ("preamble", None),
        ("education", "EDUCATION"),
        ("experience", "Research Experience"),
        ("projects", "Projects"),
        ("skills", "Skills"),
        ("coursework", "Relevant Coursework"),
    ]
    experience = sections[2]
    # A wrapped bullet is joined; nothing is reworded.
    assert experience.bullets == (
        "Built a graph neural network for protein folding that improved accuracy by 4 points.",
        "Wrote data pipelines in Python.",
    )
    assert sections[3].bullets == ("Compiler for a toy language", "Robot arm controller")
    assert sections[0].bullets == ("Asha Rao", "asha@example.edu")


def test_every_source_word_survives_the_split() -> None:
    sections = cv.split_sections(CV_TEXT)
    words_out = (
        " ".join(" ".join([s.heading or "", *s.bullets]) for s in sections).replace(":", "").split()
    )
    words_in = [word.lstrip("-*").rstrip(":") for word in CV_TEXT.split() if word not in {"-", "*"}]

    assert sorted(w for w in words_out if w) == sorted(w for w in words_in if w)


def test_an_unknown_all_caps_heading_is_kept_as_other() -> None:
    sections = cv.split_sections("HOBBIES\nChess\nskills used daily in my job as a barista")

    assert sections[0].kind == "other"
    assert sections[0].bullets == ("Chess", "skills used daily in my job as a barista")


# --------------------------------------------------------------------------- #
# original version
# --------------------------------------------------------------------------- #


def test_the_original_version_is_created_once_per_document(
    connection: sqlite3.Connection, tmp_path: Path
) -> None:
    user = account(connection)
    document_id = upload(connection, user, tmp_path)

    first = cv.create_original(
        connection, user_id=user, document_id=document_id, data_directory=tmp_path
    )
    again = cv.create_original(
        connection, user_id=user, document_id=document_id, data_directory=tmp_path
    )

    assert first == again
    assert first.kind == "original"
    assert first.parent_version_id is None and first.target_type is None
    assert first.content_sha256 == cv.content_hash(first.sections)
    assert len(cv.list_versions(connection, user_id=user)) == 1


def test_the_database_refuses_to_modify_the_original(
    connection: sqlite3.Connection, tmp_path: Path
) -> None:
    user = account(connection)
    version = cv.create_original(
        connection,
        user_id=user,
        document_id=upload(connection, user, tmp_path),
        data_directory=tmp_path,
    )

    with pytest.raises(sqlite3.IntegrityError, match="cannot be modified"):
        connection.execute(
            "UPDATE cv_versions SET sections_json = '[]' WHERE id = ?", (version.id,)
        )
    assert cv.get_version(connection, user_id=user, version_id=version.id) == version


def test_only_a_cv_document_can_be_the_source(
    connection: sqlite3.Connection, tmp_path: Path
) -> None:
    user = account(connection)
    transcript = upload(connection, user, tmp_path, kind="transcript", name="t.txt")

    with pytest.raises(CvError, match="only a CV"):
        cv.create_original(
            connection, user_id=user, document_id=transcript, data_directory=tmp_path
        )


def test_versions_are_private_to_their_owner(
    connection: sqlite3.Connection, tmp_path: Path
) -> None:
    asha = account(connection)
    ben = account(connection, email="ben@example.edu")
    version = cv.create_original(
        connection,
        user_id=asha,
        document_id=upload(connection, asha, tmp_path),
        data_directory=tmp_path,
    )

    assert cv.list_versions(connection, user_id=ben) == ()
    with pytest.raises(CvVersionNotFoundError):
        cv.get_version(connection, user_id=ben, version_id=version.id)
    with pytest.raises(documents.DocumentNotFoundError):
        cv.create_original(
            connection, user_id=ben, document_id=version.document_id, data_directory=tmp_path
        )


def test_versions_go_with_the_account_and_appear_in_the_export(
    connection: sqlite3.Connection, tmp_path: Path
) -> None:
    user = account(connection)
    cv.create_original(
        connection,
        user_id=user,
        document_id=upload(connection, user, tmp_path),
        data_directory=tmp_path,
    )

    exported = export_account_data(connection, user)["cv_versions"]
    assert exported[0]["kind"] == "original"
    assert exported[0]["sections"][1]["heading"] == "EDUCATION"

    profiles.delete_user(connection, user, actor="test")
    assert connection.execute("SELECT COUNT(*) FROM cv_versions").fetchone()[0] == 0


def test_an_empty_cv_is_rejected_with_a_reason(
    connection: sqlite3.Connection, tmp_path: Path
) -> None:
    user = account(connection)
    document_id = upload(connection, user, tmp_path, text="   \n\nSKILLS\n")

    with pytest.raises(CvError, match="no text could be read"):
        cv.create_original(
            connection, user_id=user, document_id=document_id, data_directory=tmp_path
        )
