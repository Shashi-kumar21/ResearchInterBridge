"""Phase 7c: decide on suggestions, approve a tailored version, diff (ADR-024)."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from research_inter_bridge import cv, db, profiles
from research_inter_bridge import cv_suggestions as cs
from research_inter_bridge.errors import CvError
from research_inter_bridge.profiles import StudentProfile
from tests.test_cv_suggestions import seed_professor, setup


@pytest.fixture
def connection(tmp_path: Path):
    with db.session(tmp_path / "app.db") as conn:
        db.apply_migrations(conn)
        yield conn


def run_for(connection: sqlite3.Connection, tmp_path: Path) -> tuple[int, cs.AnalysisRun]:
    professor = seed_professor(connection)
    user, version = setup(connection, tmp_path)
    run = cs.analyse(
        connection, user_id=user, version_id=version, target_type="faculty", target_id=professor
    )
    return user, run


def by_kind(run: cs.AnalysisRun, kind: str, index: int = 0) -> cs.Suggestion:
    return [s for s in run.suggestions if s.kind == kind][index]


def test_approval_needs_at_least_one_accepted_change(
    connection: sqlite3.Connection, tmp_path: Path
) -> None:
    user, run = run_for(connection, tmp_path)

    with pytest.raises(CvError) as excinfo:
        cs.approve(connection, user_id=user, run_id=run.id)
    assert excinfo.value.code == "cv_nothing_accepted"
    assert (
        connection.execute("SELECT COUNT(*) FROM cv_versions WHERE kind = 'tailored'").fetchone()[0]
        == 0
    )


def test_only_accepted_and_edited_changes_are_applied(
    connection: sqlite3.Connection, tmp_path: Path
) -> None:
    user, run = run_for(connection, tmp_path)
    cs.decide(
        connection,
        user_id=user,
        suggestion_id=by_kind(run, "reorder_sections").id,
        status="accepted",
    )
    cs.decide(
        connection, user_id=user, suggestion_id=by_kind(run, "surface_skill").id, status="rejected"
    )
    flagged = by_kind(run, "flag_vague")
    cs.decide(
        connection,
        user_id=user,
        suggestion_id=flagged.id,
        status="edited",
        edited_text="Ran deep learning experiments on 3 protein datasets",
    )
    # The bullet reorders stay pending and must not be assumed accepted.

    tailored = cs.approve(connection, user_id=user, run_id=run.id)
    original = cv.get_version(connection, user_id=user, version_id=run.version_id)

    assert tailored.kind == "tailored"
    assert tailored.parent_version_id == original.id
    assert (tailored.target_type, tailored.target_id) == ("faculty", run.target_id)
    assert tailored.approved_at is not None
    labels = [s.heading or "Introduction" for s in tailored.sections]
    assert labels == list(by_kind(run, "reorder_sections").after)
    all_lines = [line for s in tailored.sections for line in s.bullets]
    assert "Ran deep learning experiments on 3 protein datasets" in all_lines
    assert "Helped with deep learning experiments for proteins" not in all_lines
    assert "PyTorch" not in all_lines  # rejected skill
    projects = next(s for s in tailored.sections if s.kind == "projects")
    assert projects.bullets[0] == "Compiler for a toy language"  # pending reorder not applied
    # The original is untouched.
    assert cv.get_version(connection, user_id=user, version_id=original.id) == original


def test_an_accepted_skill_and_bullet_reorder_are_applied(
    connection: sqlite3.Connection, tmp_path: Path
) -> None:
    user, run = run_for(connection, tmp_path)
    for suggestion in run.suggestions:
        if suggestion.kind in ("surface_skill", "reorder_bullets"):
            cs.decide(connection, user_id=user, suggestion_id=suggestion.id, status="accepted")

    tailored = cs.approve(connection, user_id=user, run_id=run.id)

    skills = next(s for s in tailored.sections if s.kind == "skills")
    assert skills.bullets[-1] == "PyTorch"
    projects = next(s for s in tailored.sections if s.kind == "projects")
    assert projects.bullets[0].startswith("Graph neural network")


def test_approving_the_same_choices_twice_returns_one_version(
    connection: sqlite3.Connection, tmp_path: Path
) -> None:
    user, run = run_for(connection, tmp_path)
    cs.decide(
        connection, user_id=user, suggestion_id=by_kind(run, "surface_skill").id, status="accepted"
    )

    first = cs.approve(connection, user_id=user, run_id=run.id)
    again = cs.approve(connection, user_id=user, run_id=run.id)

    assert first.id == again.id
    assert (
        connection.execute("SELECT COUNT(*) FROM cv_versions WHERE kind = 'tailored'").fetchone()[0]
        == 1
    )


def test_decisions_are_validated(connection: sqlite3.Connection, tmp_path: Path) -> None:
    user, run = run_for(connection, tmp_path)
    reorder = by_kind(run, "reorder_sections").id

    with pytest.raises(CvError, match="only a skill or a flagged line"):
        cs.decide(connection, user_id=user, suggestion_id=reorder, status="edited", edited_text="x")
    with pytest.raises(CvError, match="edited_text is required"):
        cs.decide(
            connection, user_id=user, suggestion_id=by_kind(run, "flag_vague").id, status="edited"
        )
    with pytest.raises(CvError, match="only allowed when status is 'edited'"):
        cs.decide(
            connection, user_id=user, suggestion_id=reorder, status="accepted", edited_text="x"
        )
    with pytest.raises(CvError, match="status must be"):
        cs.decide(connection, user_id=user, suggestion_id=reorder, status="maybe")
    with pytest.raises(CvError) as excinfo:
        cs.decide(connection, user_id=user + 99, suggestion_id=reorder, status="accepted")
    assert excinfo.value.code == "cv_suggestion_missing"


def test_a_new_run_keeps_decisions_on_identical_suggestions_and_prunes_old_runs(
    connection: sqlite3.Connection, tmp_path: Path
) -> None:
    # QA n2 on #45.
    user, run = run_for(connection, tmp_path)
    cs.decide(
        connection,
        user_id=user,
        suggestion_id=by_kind(run, "reorder_sections").id,
        status="rejected",
    )
    profile = profiles.load_profile(connection, user)
    fields = {
        name: getattr(profile, name)
        for name in StudentProfile.__dataclass_fields__
        if name not in ("created_at", "updated_at")
    }
    profiles.save_profile(
        connection, StudentProfile(**{**fields, "coursework": ("Chemistry",)}), actor="test"
    )

    second = cs.analyse(
        connection,
        user_id=user,
        version_id=run.version_id,
        target_type="faculty",
        target_id=run.target_id,
    )

    assert second.id != run.id
    assert by_kind(second, "reorder_sections").status == "rejected"
    assert all(s.status == "pending" for s in second.suggestions if s.kind != "reorder_sections")
    assert connection.execute("SELECT COUNT(*) FROM cv_analysis_runs").fetchone()[0] == 1


def test_the_diff_shows_every_change_against_the_original(
    connection: sqlite3.Connection, tmp_path: Path
) -> None:
    user, run = run_for(connection, tmp_path)
    cs.decide(
        connection, user_id=user, suggestion_id=by_kind(run, "surface_skill").id, status="accepted"
    )
    tailored = cs.approve(connection, user_id=user, run_id=run.id)
    original = cv.get_version(connection, user_id=user, version_id=run.version_id)

    changes = cs.diff(original.sections, tailored.sections)

    assert [c for c in changes if c["op"] == "added"] == [{"op": "added", "line": "PyTorch"}]
    assert not [c for c in changes if c["op"] == "removed"]
    assert {"op": "same", "line": "## Education"} in changes


def test_approval_is_audited_with_counts_only(
    connection: sqlite3.Connection, tmp_path: Path
) -> None:
    import json

    user, run = run_for(connection, tmp_path)
    cs.decide(
        connection, user_id=user, suggestion_id=by_kind(run, "surface_skill").id, status="accepted"
    )
    cs.approve(connection, user_id=user, run_id=run.id)

    row = connection.execute(
        "SELECT metadata_json FROM audit_events WHERE action = 'cv.version_approved'"
    ).fetchone()
    assert set(json.loads(row["metadata_json"])) == {"parent_version_id", "target", "applied"}
    assert "PyTorch" not in row["metadata_json"]


def test_a_decision_can_be_undone_before_approval(
    connection: sqlite3.Connection, tmp_path: Path
) -> None:
    user, run = run_for(connection, tmp_path)
    skill = by_kind(run, "surface_skill").id
    flagged = by_kind(run, "flag_vague").id
    cs.decide(connection, user_id=user, suggestion_id=skill, status="accepted")
    cs.decide(
        connection, user_id=user, suggestion_id=flagged, status="edited", edited_text="My own words"
    )

    undone = cs.decide(connection, user_id=user, suggestion_id=skill, status="pending")
    cs.decide(connection, user_id=user, suggestion_id=flagged, status="rejected")

    assert undone.status == "pending" and undone.edited_text is None
    with pytest.raises(CvError) as excinfo:
        cs.approve(connection, user_id=user, run_id=run.id)
    assert excinfo.value.code == "cv_nothing_accepted"
    lines = [
        line for s in cs.preview(connection, user_id=user, run_id=run.id) for line in s.bullets
    ]
    assert "PyTorch" not in lines and "My own words" not in lines


def test_a_concurrent_identical_approval_returns_the_winners_version(
    connection: sqlite3.Connection, tmp_path: Path
) -> None:
    # QA n2 on #46: the partial unique index plus ON CONFLICT absorb a race.
    user, run = run_for(connection, tmp_path)
    cs.decide(
        connection, user_id=user, suggestion_id=by_kind(run, "surface_skill").id, status="accepted"
    )
    winner = cs.approve(connection, user_id=user, run_id=run.id)
    real_execute = connection.execute
    hidden = {"done": False}

    class Racing:
        def __getattr__(self, name: str):
            return getattr(connection, name)

        def execute(self, sql: str, parameters=()):  # type: ignore[no-untyped-def]
            if not hidden["done"] and sql.startswith("SELECT id FROM cv_versions WHERE user_id"):
                hidden["done"] = True
                return real_execute("SELECT id FROM cv_versions WHERE 0")
            return real_execute(sql, parameters)

    loser = cs.approve(Racing(), user_id=user, run_id=run.id)  # type: ignore[arg-type]

    assert loser.id == winner.id
    assert (
        connection.execute("SELECT COUNT(*) FROM cv_versions WHERE kind = 'tailored'").fetchone()[0]
        == 1
    )
