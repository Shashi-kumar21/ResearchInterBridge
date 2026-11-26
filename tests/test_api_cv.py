"""HTTP contract for CV versions and suggestions (Phase 7d-1, ADR-024)."""

from __future__ import annotations

import socket
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from httpx import Response

from research_inter_bridge import db, ratelimit
from research_inter_bridge import faculty as fac
from research_inter_bridge.api import create_app
from research_inter_bridge.config import Settings
from tests.test_cv_suggestions import CV_TEXT, FacultyAdapter

PASSWORD = "correct horse battery staple"
CONSENTS = ["personal_data", "document_processing"]


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(database_path=tmp_path / "api.db", data_directory=tmp_path / "data")


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def error_code(response: Response) -> str:
    return str(response.json()["error"]["code"])


def seed_professor(settings: Settings) -> int:
    with db.session(settings.database_path) as connection:
        db.apply_migrations(connection)
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


def student(client: TestClient, email: str = "asha@example.edu") -> int:
    assert (
        client.post(
            "/api/auth/register", json={"email": email, "password": PASSWORD, "consents": CONSENTS}
        ).status_code
        == 201
    )
    assert (
        client.put(
            "/api/profile",
            json={
                "full_name": "Asha Rao",
                "contact_email": email,
                "degree_type": "msc",
                "skills": ["PyTorch", "Rust"],
                "research_keywords": ["deep learning"],
            },
        ).status_code
        == 200
    )
    uploaded = client.post(
        "/api/documents",
        files={"file": ("cv.txt", CV_TEXT.encode("utf-8"), "text/plain")},
        data={"kind": "cv"},
    )
    assert uploaded.status_code == 201, uploaded.text
    return int(uploaded.json()["id"])


def analyse(client: TestClient, document_id: int, professor: int) -> dict:
    original = client.post("/api/cv/originals", json={"document_id": document_id})
    assert original.status_code == 200, original.text
    run = client.post(
        "/api/cv/analyses",
        json={
            "version_id": original.json()["id"],
            "target_type": "faculty",
            "target_id": professor,
        },
    )
    assert run.status_code == 200, run.text
    return run.json()


def first(run: dict, kind: str) -> dict:
    return next(item for item in run["suggestions"] if item["kind"] == kind)


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/api/cv/versions"),
        ("post", "/api/cv/originals"),
        ("post", "/api/cv/analyses"),
        ("get", "/api/cv/analyses/1"),
        ("patch", "/api/cv/suggestions/1"),
        ("get", "/api/cv/analyses/1/preview"),
        ("post", "/api/cv/analyses/1/approve"),
    ],
)
def test_cv_routes_require_a_session(client: TestClient, method: str, path: str) -> None:
    kwargs = {"json": {}} if method in ("post", "patch") else {}

    response = getattr(client, method)(path, **kwargs)

    assert response.status_code == 401
    assert error_code(response) == "invalid_session"


def test_the_full_flow_decides_previews_and_approves(
    client: TestClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    professor = seed_professor(settings)
    document_id = student(client)

    def no_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("CV routes must not call out")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    run = analyse(client, document_id, professor)
    again = client.post("/api/cv/originals", json={"document_id": document_id})
    skill = first(run, "surface_skill")

    decided = client.patch(f"/api/cv/suggestions/{skill['id']}", json={"status": "accepted"})
    preview = client.get(f"/api/cv/analyses/{run['id']}/preview")
    approved = client.post(f"/api/cv/analyses/{run['id']}/approve")
    versions = client.get("/api/cv/versions").json()["versions"]

    assert again.json()["id"] == run["version_id"]
    assert run["target_name"] == "Jane Doe"
    assert all(item["evidence"] for item in run["suggestions"])
    assert decided.json()["status"] == "accepted"
    assert preview.json()["applied"] == 1
    assert {"op": "added", "line": "PyTorch"} in preview.json()["diff"]
    assert approved.status_code == 200
    version = approved.json()["version"]
    assert version["kind"] == "tailored"
    assert version["parent_version_id"] == run["version_id"]
    assert version["approved_at"] is not None
    assert [v["kind"] for v in versions] == ["original", "tailored"]
    assert (
        versions[0]["sections"] == client.get("/api/cv/versions").json()["versions"][0]["sections"]
    )


def test_approval_conflicts_are_explained(client: TestClient, settings: Settings) -> None:
    professor = seed_professor(settings)
    run = analyse(client, student(client), professor)

    nothing = client.post(f"/api/cv/analyses/{run['id']}/approve")
    flag = first(run, "flag_vague")
    client.patch(f"/api/cv/suggestions/{flag['id']}", json={"status": "accepted"})
    unchanged = client.post(f"/api/cv/analyses/{run['id']}/approve")

    assert nothing.status_code == 409
    assert error_code(nothing) == "cv_nothing_accepted"
    # QA n1 on #46: an unedited flag changes nothing, so no identical copy.
    assert unchanged.status_code == 409
    assert error_code(unchanged) == "cv_nothing_changed"


def test_an_edit_and_an_undo_round_trip(client: TestClient, settings: Settings) -> None:
    professor = seed_professor(settings)
    run = analyse(client, student(client), professor)
    flag = first(run, "flag_vague")

    edited = client.patch(
        f"/api/cv/suggestions/{flag['id']}",
        json={"status": "edited", "edited_text": "Ran experiments on 3 protein datasets"},
    )
    undone = client.patch(f"/api/cv/suggestions/{flag['id']}", json={"status": "pending"})

    assert edited.json()["edited_text"] == "Ran experiments on 3 protein datasets"
    assert undone.json()["status"] == "pending"
    assert undone.json()["edited_text"] is None


def test_invalid_bodies_are_validation_errors(client: TestClient, settings: Settings) -> None:
    professor = seed_professor(settings)
    run = analyse(client, student(client), professor)
    flag = first(run, "flag_vague")["id"]

    for body in (
        {"status": "maybe"},
        {"status": "edited", "edited_text": "x" * 501},
        {"status": "accepted", "extra": True},
    ):
        response = client.patch(f"/api/cv/suggestions/{flag}", json=body)
        assert response.status_code == 422
        assert error_code(response) == "invalid_request"
    wrong_target = client.post(
        "/api/cv/analyses",
        json={"version_id": run["version_id"], "target_type": "lab", "target_id": 1},
    )
    assert wrong_target.status_code == 422


def test_cv_data_is_private_to_its_owner(settings: Settings, client: TestClient) -> None:
    professor = seed_professor(settings)
    run = analyse(client, student(client), professor)
    suggestion = run["suggestions"][0]["id"]

    with TestClient(create_app(settings)) as other:
        student(other, email="ben@example.edu")
        assert other.get(f"/api/cv/analyses/{run['id']}").status_code == 404
        assert error_code(other.get(f"/api/cv/analyses/{run['id']}/preview")) == "cv_run_missing"
        response = other.patch(f"/api/cv/suggestions/{suggestion}", json={"status": "accepted"})
        assert response.status_code == 404
        assert error_code(response) == "cv_suggestion_missing"
        foreign = other.post(
            "/api/cv/analyses",
            json={
                "version_id": run["version_id"],
                "target_type": "faculty",
                "target_id": professor,
            },
        )
        assert error_code(foreign) == "cv_version_missing"


def test_cv_writes_share_a_per_account_rate_limit(
    client: TestClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    professor = seed_professor(settings)
    document_id = student(client)
    monkeypatch.setattr(
        ratelimit, "CV_RULE", ratelimit.RateLimitRule(name="cv.write", limit=2, window_seconds=300)
    )

    run = analyse(client, document_id, professor)  # spends 2 units
    refused = client.patch(
        f"/api/cv/suggestions/{run['suggestions'][0]['id']}", json={"status": "accepted"}
    )
    reading = client.get(f"/api/cv/analyses/{run['id']}")

    assert refused.status_code == 429
    assert error_code(refused) == "rate_limited"
    assert reading.status_code == 200


def test_a_suggestion_from_a_pruned_run_is_missing(client: TestClient, settings: Settings) -> None:
    # QA n3 on #46: the screen reloads the latest run on this code.
    professor = seed_professor(settings)
    run = analyse(client, student(client), professor)
    stale = run["suggestions"][0]["id"]
    client.put(
        "/api/profile",
        json={
            "full_name": "Asha Rao",
            "contact_email": "asha@example.edu",
            "degree_type": "msc",
            "skills": ["PyTorch", "Rust"],
            "research_keywords": ["deep learning"],
            "coursework": ["Chemistry"],
        },
    )
    client.post(
        "/api/cv/analyses",
        json={"version_id": run["version_id"], "target_type": "faculty", "target_id": professor},
    )

    response = client.patch(f"/api/cv/suggestions/{stale}", json={"status": "accepted"})

    assert response.status_code == 404
    assert error_code(response) == "cv_suggestion_missing"
