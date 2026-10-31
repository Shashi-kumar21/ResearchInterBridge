"""CLI surface for the fact-confirmation workflow."""

from __future__ import annotations

import json
from pathlib import Path

import pymupdf

from research_inter_bridge import db, profiles


def _pdf(path: Path, text: str) -> Path:
    document = pymupdf.open()
    document.new_page().insert_text((72, 72), text, fontsize=11)
    path.write_bytes(document.tobytes())
    document.close()
    return path


def _seed(cli, tmp_path: Path, *texts: str) -> tuple[int, list[dict[str, object]]]:
    _code, out = cli.run("user-create")
    user_id = json.loads(out)["user_id"]
    for index, text in enumerate(texts):
        code, _out = cli.run(
            "document-add",
            "--user-id",
            str(user_id),
            "--file",
            str(_pdf(tmp_path / f"cv{index}.pdf", f"Asha Verma\n{text}")),
            "--parse",
        )
        assert code == 0
    code, out = cli.run("fact-list", "--user-id", str(user_id))
    assert code == 0
    return user_id, json.loads(out)["fields"]


def _create_profile(cli, user_id: int) -> None:
    with db.session(cli.settings.database_path) as connection:
        profiles.save_profile(
            connection,
            profiles.StudentProfile(
                user_id=user_id, full_name="Asha Verma", contact_email="asha@example.edu"
            ),
            actor=f"student:{user_id}",
        )


def test_fact_list_flags_conflicts_between_two_documents(cli, tmp_path: Path) -> None:
    user_id, fields = _seed(cli, tmp_path, "CGPA: 8.9/10", "CGPA: 8.4/10")

    gpa = next(field for field in fields if field["field"] == "gpa")
    assert gpa["has_conflict"] is True
    assert len(gpa["pending"]) == 2
    assert gpa["confirmed"] == []
    assert user_id > 0


def test_confirm_with_correction_then_apply_to_profile(cli, tmp_path: Path) -> None:
    user_id, fields = _seed(cli, tmp_path, "CGPA: 8.9/10\nPhone: +91 98765 43210")
    _create_profile(cli, user_id)
    gpa = next(field for field in fields if field["field"] == "gpa")
    phone = next(field for field in fields if field["field"] == "phone")
    fact_id = gpa["pending"][0]["id"]

    code, _out = cli.run(
        "fact-confirm", "--user-id", str(user_id), "--fact-id", str(fact_id), "--value", "9.1/10"
    )
    assert code == 0
    code, _out = cli.run(
        "fact-confirm", "--user-id", str(user_id), "--fact-id", str(phone["pending"][0]["id"])
    )
    assert code == 0

    code, out = cli.run("fact-apply", "--user-id", str(user_id))
    assert code == 0
    assert set(json.loads(out)["applied"]) == {"gpa", "gpa_scale", "phone"}

    with db.session(cli.settings.database_path) as connection:
        stored = profiles.load_profile(connection, user_id)
    assert stored.gpa == 9.1
    assert stored.gpa_scale == 10.0
    assert stored.phone == "+91 98765 43210"


def test_fact_reject_removes_the_candidate(cli, tmp_path: Path) -> None:
    user_id, fields = _seed(cli, tmp_path, "CGPA: 8.9/10")
    gpa = next(field for field in fields if field["field"] == "gpa")
    fact_id = gpa["pending"][0]["id"]

    code, _out = cli.run("fact-reject", "--user-id", str(user_id), "--fact-id", str(fact_id))
    assert code == 0

    _code, out = cli.run("fact-list", "--user-id", str(user_id))
    assert json.loads(out)["fields"] == []


def test_unknown_fact_id_exits_non_zero(cli, tmp_path: Path) -> None:
    user_id, _fields = _seed(cli, tmp_path, "CGPA: 8.9/10")

    code, _out = cli.run("fact-confirm", "--user-id", str(user_id), "--fact-id", "999")
    assert code == 1

    code, _out = cli.run("fact-reject", "--user-id", str(user_id), "--fact-id", "999")
    assert code == 1


def test_fact_apply_without_a_profile_reports_an_error(cli, tmp_path: Path) -> None:
    user_id, fields = _seed(cli, tmp_path, "CGPA: 8.9/10")
    fact_id = next(field for field in fields if field["field"] == "gpa")["pending"][0]["id"]
    cli.run("fact-confirm", "--user-id", str(user_id), "--fact-id", str(fact_id))

    code, _out = cli.run("fact-apply", "--user-id", str(user_id))

    assert code == 1
