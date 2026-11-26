from __future__ import annotations

import json
import socket
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest

from research_inter_bridge import consent, cv, db, documents, profiles
from research_inter_bridge import cv_suggestions as cs
from research_inter_bridge import faculty as fac
from research_inter_bridge.auth import register
from research_inter_bridge.errors import CvError, CvVersionNotFoundError
from research_inter_bridge.profiles import DegreeType, StudentProfile

CV_TEXT = """Asha Rao

Education
MSc Computer Science, Example University

Skills
C++, SQL

Projects
- Compiler for a toy language
- Graph neural network for protein folding with 92% accuracy

Experience
- Worked on data pipelines
- Helped with deep learning experiments for proteins

Hobbies
Chess
"""


@pytest.fixture
def connection(tmp_path: Path):
    with db.session(tmp_path / "app.db") as conn:
        db.apply_migrations(conn)
        yield conn


@dataclass
class FacultyAdapter:
    records: tuple[fac.DiscoveredFaculty, ...]
    name: str = "fixture"
    parser_version: str = "1"
    contact_authority: str = "official"

    def search(self, query: fac.FacultyQuery) -> tuple[fac.DiscoveredFaculty, ...]:
        return self.records


def seed_professor(connection: sqlite3.Connection) -> int:
    (member,) = fac.search_faculty(
        connection,
        fac.FacultyQuery(),
        adapter=FacultyAdapter(
            records=(
                fac.DiscoveredFaculty(
                    full_name="Jane Doe",
                    institution="Example University",
                    country="Germany",
                    department="Computer Science",
                    profile_url="https://www.cs.example.edu/people/jane",
                    research_interests=("deep learning", "proteins", "PyTorch"),
                ),
            )
        ),
        now=datetime.now(UTC),
    ).faculty
    return member.id


def setup(connection: sqlite3.Connection, tmp_path: Path, **profile: object) -> tuple[int, int]:
    user = register(
        connection,
        email="asha@example.edu",
        password="correct horse battery staple",
        consents=("personal_data", "document_processing"),
    ).user_id
    consent.record_consent(connection, user_id=user, kind="document_processing")
    values: dict[str, object] = {
        "user_id": user,
        "full_name": "Asha Rao",
        "contact_email": "asha@example.edu",
        "degree_type": DegreeType.MSC,
        "skills": ("PyTorch", "Rust"),
        "research_keywords": ("deep learning",),
    }
    values.update(profile)
    profiles.save_profile(connection, StudentProfile(**values), actor="test")  # type: ignore[arg-type]
    record = documents.save_document(
        connection,
        user_id=user,
        display_name="cv.txt",
        payload=CV_TEXT.encode("utf-8"),
        data_directory=tmp_path,
        actor=f"user:{user}",
    )
    version = cv.create_original(
        connection, user_id=user, document_id=record.id, data_directory=tmp_path
    )
    return user, version.id


def kinds(run: cs.AnalysisRun) -> list[str]:
    return [suggestion.kind for suggestion in run.suggestions]


def test_suggestions_reorder_surface_and_flag_with_evidence(
    connection: sqlite3.Connection, tmp_path: Path
) -> None:
    professor = seed_professor(connection)
    user, version = setup(connection, tmp_path)

    run = cs.analyse(
        connection, user_id=user, version_id=version, target_type="faculty", target_id=professor
    )

    assert run.target_name == "Jane Doe"
    assert set(kinds(run)) == {"reorder_sections", "reorder_bullets", "surface_skill", "flag_vague"}
    assert all(s.status == "pending" and s.evidence for s in run.suggestions)
    sections = next(s for s in run.suggestions if s.kind == "reorder_sections")
    # Introduction and Education keep their place; Experience moves ahead.
    assert sections.after[:2] == ("Introduction", "Education")
    assert sections.after.index("Experience") < sections.after.index("Skills")
    reorders = {s.before[0]: s for s in run.suggestions if s.kind == "reorder_bullets"}
    projects = reorders["Compiler for a toy language"]
    experience = reorders["Worked on data pipelines"]
    assert projects.after[0] == "Graph neural network for protein folding with 92% accuracy"
    assert experience.after[0] == "Helped with deep learning experiments for proteins"
    for suggestion in reorders.values():
        # A reorder never adds, drops or changes a line.
        assert sorted(suggestion.after) == sorted(suggestion.before)


def test_only_confirmed_profile_items_are_surfaced(
    connection: sqlite3.Connection, tmp_path: Path
) -> None:
    professor = seed_professor(connection)
    user, version = setup(connection, tmp_path)

    run = cs.analyse(
        connection, user_id=user, version_id=version, target_type="faculty", target_id=professor
    )
    surfaced = [s for s in run.suggestions if s.kind == "surface_skill"]

    # PyTorch is a confirmed profile skill matching the professor and missing
    # from the CV; Rust matches nothing; "deep learning" is already in the CV.
    assert [s.after for s in surfaced] == [("PyTorch",)]
    assert "your profile (skills): PyTorch" in surfaced[0].evidence
    assert "Add it only if it is accurate" in surfaced[0].reason


def test_no_suggestion_contains_text_the_student_did_not_write(
    connection: sqlite3.Connection, tmp_path: Path
) -> None:
    professor = seed_professor(connection)
    user, version = setup(connection, tmp_path)
    run = cs.analyse(
        connection, user_id=user, version_id=version, target_type="faculty", target_id=professor
    )
    source = cv.get_version(connection, user_id=user, version_id=version)
    own_lines = {line for section in source.sections for line in section.bullets}
    own_labels = {section.heading or "Introduction" for section in source.sections}
    confirmed = {"PyTorch", "Rust", "deep learning"}

    for suggestion in run.suggestions:
        for line in suggestion.after:
            assert line in own_lines | own_labels | confirmed, (suggestion.kind, line)


def test_a_vague_result_is_flagged_but_never_rewritten(
    connection: sqlite3.Connection, tmp_path: Path
) -> None:
    professor = seed_professor(connection)
    user, version = setup(connection, tmp_path)

    run = cs.analyse(
        connection, user_id=user, version_id=version, target_type="faculty", target_id=professor
    )
    flagged = [s for s in run.suggestions if s.kind == "flag_vague"]

    assert [s.before for s in flagged] == [("Helped with deep learning experiments for proteins",)]
    assert all(s.before == s.after for s in flagged)
    # A line with a number already states a result and is not flagged.
    assert all("92%" not in s.before[0] for s in flagged)


def test_the_original_is_unchanged_and_runs_are_reused(
    connection: sqlite3.Connection, tmp_path: Path
) -> None:
    professor = seed_professor(connection)
    user, version = setup(connection, tmp_path)
    before = cv.get_version(connection, user_id=user, version_id=version)

    first = cs.analyse(
        connection, user_id=user, version_id=version, target_type="faculty", target_id=professor
    )
    again = cs.analyse(
        connection, user_id=user, version_id=version, target_type="faculty", target_id=professor
    )

    assert again.id == first.id
    assert cv.get_version(connection, user_id=user, version_id=version) == before
    assert connection.execute("SELECT COUNT(*) FROM cv_analysis_runs").fetchone()[0] == 1
    assert connection.execute("SELECT COUNT(*) FROM cv_versions").fetchone()[0] == 1


def test_a_profile_change_produces_a_new_run(
    connection: sqlite3.Connection, tmp_path: Path
) -> None:
    professor = seed_professor(connection)
    user, version = setup(connection, tmp_path)
    first = cs.analyse(
        connection, user_id=user, version_id=version, target_type="faculty", target_id=professor
    )
    profile = profiles.load_profile(connection, user)
    fields = {
        name: getattr(profile, name)
        for name in StudentProfile.__dataclass_fields__
        if name not in ("created_at", "updated_at")
    }
    profiles.save_profile(
        connection, StudentProfile(**{**fields, "skills": ("Rust",)}), actor="test"
    )

    second = cs.analyse(
        connection, user_id=user, version_id=version, target_type="faculty", target_id=professor
    )

    assert second.id != first.id
    assert "surface_skill" not in kinds(second)


def test_analysis_makes_no_outbound_call_and_audits_counts_only(
    connection: sqlite3.Connection, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    professor = seed_professor(connection)
    user, version = setup(connection, tmp_path)

    def no_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("CV analysis must not call out")

    monkeypatch.setattr(socket.socket, "connect", no_network)

    cs.analyse(
        connection, user_id=user, version_id=version, target_type="faculty", target_id=professor
    )

    row = connection.execute(
        "SELECT metadata_json FROM audit_events WHERE action = 'cv.analysed'"
    ).fetchone()
    metadata = json.loads(row["metadata_json"])
    assert set(metadata) == {"target", "suggestions", "rules_version"}
    assert "protein" not in row["metadata_json"].casefold()


def test_runs_are_private_and_validate_their_inputs(
    connection: sqlite3.Connection, tmp_path: Path
) -> None:
    professor = seed_professor(connection)
    user, version = setup(connection, tmp_path)
    run = cs.analyse(
        connection, user_id=user, version_id=version, target_type="faculty", target_id=professor
    )
    other = register(
        connection,
        email="ben@example.edu",
        password="correct horse battery staple",
        consents=("personal_data", "document_processing"),
    ).user_id

    with pytest.raises(CvError) as excinfo:
        cs.get_run(connection, user_id=other, run_id=run.id)
    assert excinfo.value.code == "cv_run_missing"
    with pytest.raises(CvVersionNotFoundError):
        cs.analyse(
            connection,
            user_id=other,
            version_id=version,
            target_type="faculty",
            target_id=professor,
        )
    with pytest.raises(CvError, match="target_type"):
        cs.analyse(
            connection, user_id=user, version_id=version, target_type="lab", target_id=professor
        )


def test_new_heading_aliases_keep_lines_out_of_skills() -> None:
    # QA nit on #44.
    text = "Skills\nPython\nCertifications\nAWS Certified\nReferences\nOn request"
    sections = cv.split_sections(text)

    assert [s.kind for s in sections] == ["skills", "certifications", "references"]
    assert sections[0].bullets == ("Python",)


def test_runs_and_suggestions_are_in_the_account_export(
    connection: sqlite3.Connection, tmp_path: Path
) -> None:
    # QA B1 on #45: suggestions quote the student's CV lines.
    from research_inter_bridge.auth import export_account_data

    professor = seed_professor(connection)
    user, version = setup(connection, tmp_path)
    run = cs.analyse(
        connection, user_id=user, version_id=version, target_type="faculty", target_id=professor
    )

    (exported,) = export_account_data(connection, user)["cv_analyses"]
    assert exported["id"] == run.id
    assert exported["target_id"] == professor
    assert len(exported["suggestions"]) == len(run.suggestions)
    lines = [line for item in exported["suggestions"] for line in item["before"]]
    assert "Worked on data pipelines" in lines
    profiles.delete_user(connection, user, actor="test")
    assert connection.execute("SELECT COUNT(*) FROM cv_suggestions").fetchone()[0] == 0


def test_a_long_section_does_not_win_by_length() -> None:
    # QA n1 on #45: relevance counts distinct terms, not line hits.
    terms = ("protein", "graph")
    short = cv.CvSection("projects", "Projects", ("Protein graph model",))
    long = cv.CvSection(
        "experience", "Experience", tuple(f"protein lab shift {i}" for i in range(5))
    )

    assert cs._section_relevance(short, terms)[0] == 2.0
    assert cs._section_relevance(long, terms)[0] == 1.0


def test_an_identical_concurrent_run_is_reused_not_duplicated(
    connection: sqlite3.Connection, tmp_path: Path
) -> None:
    # QA n3 on #45: the unique index plus ON CONFLICT absorb a race.
    professor = seed_professor(connection)
    user, version = setup(connection, tmp_path)
    first = cs.analyse(
        connection, user_id=user, version_id=version, target_type="faculty", target_id=professor
    )
    real_execute = connection.execute
    hidden = {"done": False}

    class Racing:
        def __getattr__(self, name: str):
            return getattr(connection, name)

        def execute(self, sql: str, parameters=()):  # type: ignore[no-untyped-def]
            if not hidden["done"] and sql.startswith("SELECT id FROM cv_analysis_runs"):
                hidden["done"] = True
                return real_execute("SELECT id FROM cv_analysis_runs WHERE 0")
            return real_execute(sql, parameters)

    again = cs.analyse(
        Racing(),  # type: ignore[arg-type]
        user_id=user,
        version_id=version,
        target_type="faculty",
        target_id=professor,
    )

    assert again.id == first.id
    assert connection.execute("SELECT COUNT(*) FROM cv_analysis_runs").fetchone()[0] == 1
