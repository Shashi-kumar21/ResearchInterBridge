"""Phase 8a: professor-specific drafts from a template and verified facts (ADR-026)."""

from __future__ import annotations

import json
import socket
import sqlite3
from datetime import UTC, date, datetime
from pathlib import Path

import pytest

from research_inter_bridge import consent, cv, db, documents, drafts, profiles
from research_inter_bridge import faculty as fac
from research_inter_bridge.auth import export_account_data, register
from research_inter_bridge.errors import DraftError
from research_inter_bridge.profiles import DegreeType, StudentProfile
from tests.test_cv_suggestions import CV_TEXT, FacultyAdapter

PROFILE_URL = "https://www.cs.example.edu/people/jane"


@pytest.fixture
def connection(tmp_path: Path):
    with db.session(tmp_path / "app.db") as conn:
        db.apply_migrations(conn)
        yield conn


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("drafting must not call out")

    monkeypatch.setattr(socket.socket, "connect", refuse)


def professor(
    connection: sqlite3.Connection, query: fac.FacultyQuery | None = None, **overrides: object
) -> int:
    values: dict[str, object] = {
        "full_name": "Jane Doe",
        "title": "Associate Professor",
        "institution": "Example University",
        "country": "Germany",
        "department": "Computer Science",
        "profile_url": PROFILE_URL,
        "email": "jane.doe@cs.example.edu",
        "email_source_url": PROFILE_URL,
        "research_interests": ("deep learning", "proteins"),
        "publications": (
            fac.DiscoveredPublication(
                title="Protein Folding with Deep Learning", doi="10.1234/pf", year=2025
            ),
        ),
    }
    values.update(overrides)
    (member,) = fac.search_faculty(
        connection,
        # A distinct query per professor: an identical one is served from the cache.
        query or fac.FacultyQuery(),
        adapter=FacultyAdapter(records=(fac.DiscoveredFaculty(**values),)),  # type: ignore[arg-type]
        now=datetime.now(UTC),
    ).faculty
    return member.id


def student(
    connection: sqlite3.Connection,
    tmp_path: Path,
    email: str = "asha@example.edu",
    **fields: object,
) -> int:
    user = register(
        connection,
        email=email,
        password="correct horse battery staple",
        consents=("personal_data", "document_processing"),
    ).user_id
    consent.record_consent(connection, user_id=user, kind="document_processing")
    values: dict[str, object] = {
        "user_id": user,
        "full_name": "Asha Rao",
        "contact_email": email,
        "degree_type": DegreeType.MSC,
        "institution": "Example Institute",
        "research_keywords": ("deep learning", "proteins"),
        "internship_start": date(2027, 5, 1),
        "internship_end": date(2027, 7, 31),
    }
    values.update(fields)
    profiles.save_profile(connection, StudentProfile(**values), actor="test")  # type: ignore[arg-type]
    record = documents.save_document(
        connection,
        user_id=user,
        display_name="cv.txt",
        payload=CV_TEXT.encode("utf-8"),
        data_directory=tmp_path,
        actor=f"user:{user}",
    )
    cv.create_original(connection, user_id=user, document_id=record.id, data_directory=tmp_path)
    return user


def test_a_generated_draft_uses_only_verified_facts_with_evidence(
    connection: sqlite3.Connection, tmp_path: Path
) -> None:
    faculty_id = professor(connection)
    user = student(connection, tmp_path)

    draft = drafts.generate(connection, user_id=user, faculty_id=faculty_id)
    version = draft.current

    assert version.subject == "Research internship inquiry: Protein Folding with Deep Learning"
    assert version.body.startswith("Dear Professor Jane Doe,")
    assert (
        'Your paper "Protein Folding with Deep Learning" (2025) caught my interest' in version.body
    )
    assert "My name is Asha Rao, a M.Sc. student at Example Institute." in version.body
    assert "from May 2027 to July 2027" in version.body
    # Only the student's own CV lines that match the professor are quoted.
    assert "- Helped with deep learning experiments for proteins" in version.body
    assert "Compiler for a toy language" not in version.body
    assert version.recipient == "jane.doe@cs.example.edu"
    assert version.cv_version_id is not None
    assert {
        "claim": "Paper: Protein Folding with Deep Learning",
        "source": "https://doi.org/10.1234/pf",
    } in version.evidence
    assert version.flags == ()


def test_the_template_never_claims_reading_or_loving(
    connection: sqlite3.Connection, tmp_path: Path
) -> None:
    faculty_id = professor(connection)
    user = student(connection, tmp_path)

    body = drafts.generate(connection, user_id=user, faculty_id=faculty_id).current.body.casefold()

    for claim in ("i read", "i loved", "i have read", "big fan", "admire"):
        assert claim not in body


def test_missing_details_are_flagged_not_guessed(
    connection: sqlite3.Connection, tmp_path: Path
) -> None:
    faculty_id = professor(
        connection,
        title=None,
        email=None,
        email_source_url=None,
        publications=(),
        research_interests=("medieval history",),
    )
    user = student(
        connection,
        tmp_path,
        institution=None,
        degree_type=None,
        internship_start=None,
        internship_end=None,
    )

    version = drafts.generate(connection, user_id=user, faculty_id=faculty_id).current

    assert version.body.startswith("Dear Jane Doe,")  # no guessed title
    assert "Your work on medieval history caught my interest" in version.body
    assert version.recipient is None
    assert "Dear Dr." not in version.body
    flags = " ".join(version.flags)
    for expected in (
        "No attributed paper",
        "No verified, current email",
        "no degree or institution",
        "No line of your CV",
    ):
        assert expected in flags


def test_regenerate_and_edit_create_versions_and_history_is_bounded(
    connection: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(drafts, "MAX_VERSIONS", 3)
    faculty_id = professor(connection)
    user = student(connection, tmp_path)
    draft = drafts.generate(connection, user_id=user, faculty_id=faculty_id)

    edited = drafts.edit(
        connection,
        user_id=user,
        draft_id=draft.id,
        subject="  My   subject ",
        body="Hello\r\nthere  \n",
    )
    same = drafts.edit(
        connection, user_id=user, draft_id=draft.id, subject="My subject", body="Hello\nthere"
    )
    for index in range(3):
        drafts.edit(
            connection, user_id=user, draft_id=draft.id, subject=f"S{index}", body=f"B{index}"
        )
    final = drafts.get_draft(connection, user_id=user, draft_id=draft.id)

    assert (edited.current.subject, edited.current.body) == ("My subject", "Hello\nthere")
    assert edited.current.source == "edited"
    assert same.current.version_no == edited.current.version_no  # no-op edit
    assert [v.version_no for v in final.history] == [5, 4, 3]
    assert len(final.history) == 3
    # Recipient and attachment carry over to edits.
    assert final.current.recipient == "jane.doe@cs.example.edu"


def test_edits_are_validated(connection: sqlite3.Connection, tmp_path: Path) -> None:
    faculty_id = professor(connection)
    user = student(connection, tmp_path)
    draft = drafts.generate(connection, user_id=user, faculty_id=faculty_id)

    with pytest.raises(DraftError, match="must not be empty"):
        drafts.edit(connection, user_id=user, draft_id=draft.id, subject=" ", body="x")
    with pytest.raises(DraftError, match="subject must be at most"):
        drafts.edit(connection, user_id=user, draft_id=draft.id, subject="x" * 201, body="x")
    with pytest.raises(DraftError, match="body must be at most"):
        drafts.edit(connection, user_id=user, draft_id=draft.id, subject="x", body="x" * 10_001)


def test_a_cv_approved_for_someone_else_cannot_be_attached(
    connection: sqlite3.Connection, tmp_path: Path
) -> None:
    faculty_id = professor(connection)
    user = student(connection, tmp_path)
    original = cv.list_versions(connection, user_id=user)[0]
    stamp = datetime.now(UTC).isoformat()
    connection.execute(
        "INSERT INTO cv_versions (user_id, document_id, kind, parent_version_id, target_type, "
        "target_id, sections_json, content_sha256, created_at, approved_at) "
        "VALUES (?, ?, 'tailored', ?, 'faculty', ?, '[]', 'x', ?, ?)",
        (user, original.document_id, original.id, faculty_id + 99, stamp, stamp),
    )
    other_version = int(connection.execute("SELECT MAX(id) FROM cv_versions").fetchone()[0])

    with pytest.raises(DraftError) as excinfo:
        drafts.generate(
            connection, user_id=user, faculty_id=faculty_id, cv_version_id=other_version
        )
    assert excinfo.value.code == "draft_wrong_cv"


def test_identical_bodies_are_found_by_the_indexed_hash(
    connection: sqlite3.Connection, tmp_path: Path
) -> None:
    first = professor(connection)
    second = professor(
        connection,
        fac.FacultyQuery(country="Germany"),
        full_name="Ravi Kumar",
        profile_url="https://www.cs.example.edu/people/ravi",
        email_source_url="https://www.cs.example.edu/people/ravi",
        email="ravi@cs.example.edu",
    )
    user = student(connection, tmp_path)
    a = drafts.generate(connection, user_id=user, faculty_id=first)
    b = drafts.generate(connection, user_id=user, faculty_id=second)
    assert a.id != b.id
    assert drafts.duplicates(connection, user_id=user, draft_id=a.id) == ()

    drafts.edit(connection, user_id=user, draft_id=a.id, subject="Hi", body="Same body, here!")
    drafts.edit(connection, user_id=user, draft_id=b.id, subject="Hi", body="same   BODY here")

    assert drafts.duplicates(connection, user_id=user, draft_id=a.id) == (b.id,)
    plan = connection.execute(
        "EXPLAIN QUERY PLAN SELECT draft_id FROM draft_versions "
        "WHERE user_id = ? AND body_norm_sha256 = ?",
        (user, "x"),
    ).fetchall()
    assert any("idx_draft_versions_body" in str(row["detail"]) for row in plan)


def test_drafts_are_private_exported_audited_without_text_and_deleted_with_the_account(
    connection: sqlite3.Connection, tmp_path: Path
) -> None:
    faculty_id = professor(connection)
    user = student(connection, tmp_path)
    other = student(connection, tmp_path, email="ben@example.edu")
    draft = drafts.generate(connection, user_id=user, faculty_id=faculty_id)

    with pytest.raises(DraftError) as excinfo:
        drafts.get_draft(connection, user_id=other, draft_id=draft.id)
    assert excinfo.value.code == "draft_missing"
    assert drafts.list_drafts(connection, user_id=other) == ()

    exported = export_account_data(connection, user)["email_drafts"]
    assert exported[0]["versions"][0]["subject"] == draft.current.subject
    audit = connection.execute(
        "SELECT metadata_json FROM audit_events WHERE action = 'draft.generated'"
    ).fetchone()
    assert set(json.loads(audit["metadata_json"])) == {"version_no", "content_sha256"}
    assert "Protein" not in audit["metadata_json"]

    profiles.delete_user(connection, user, actor="test")
    assert connection.execute("SELECT COUNT(*) FROM email_drafts").fetchone()[0] == 0
    assert connection.execute("SELECT COUNT(*) FROM draft_versions").fetchone()[0] == 0


def test_the_content_hash_covers_subject_body_recipient_and_attachment() -> None:
    base = drafts.content_hash("s", "b", "r@x.edu", 1)

    assert (
        len(
            {
                base,
                drafts.content_hash("s2", "b", "r@x.edu", 1),
                drafts.content_hash("s", "b2", "r@x.edu", 1),
                drafts.content_hash("s", "b", "q@x.edu", 1),
                drafts.content_hash("s", "b", "r@x.edu", 2),
            }
        )
        == 5
    )
