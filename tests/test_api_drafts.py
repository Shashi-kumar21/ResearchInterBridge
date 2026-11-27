"""HTTP contract for email drafts and approval (Phase 8c, ADR-026)."""

from __future__ import annotations

import json
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
PROFILE_URL = "https://www.cs.example.edu/people/jane"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(database_path=tmp_path / "api.db", data_directory=tmp_path / "data")


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Refuse every outbound connection; the event loop's own loopback pair is fine."""

    real_connect = socket.socket.connect

    def refuse(self: socket.socket, address: object) -> None:
        host = address[0] if isinstance(address, tuple) else address
        if host in ("127.0.0.1", "::1", "localhost"):
            return real_connect(self, address)  # type: ignore[arg-type]
        raise AssertionError(f"draft routes must not call out (tried {host!r})")

    monkeypatch.setattr(socket.socket, "connect", refuse)


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
                        title="Professor",
                        institution="Example University",
                        country="Germany",
                        department="Computer Science",
                        profile_url=PROFILE_URL,
                        email="jane.doe@cs.example.edu",
                        email_source_url=PROFILE_URL,
                        research_interests=("deep learning", "proteins"),
                        publications=(
                            fac.DiscoveredPublication(
                                title="Protein Folding with Deep Learning",
                                doi="10.1234/pf",
                                year=2025,
                            ),
                        ),
                    ),
                )
            ),
            now=datetime.now(UTC),
        ).faculty
    return member.id


def student(client: TestClient, email: str = "asha@example.edu") -> None:
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
                "institution": "Example Institute",
                "research_keywords": ["deep learning", "proteins"],
            },
        ).status_code
        == 200
    )
    uploaded = client.post(
        "/api/documents",
        files={"file": ("cv.txt", CV_TEXT.encode("utf-8"), "text/plain")},
        data={"kind": "cv"},
    )
    assert uploaded.status_code == 201
    assert (
        client.post("/api/cv/originals", json={"document_id": uploaded.json()["id"]}).status_code
        == 200
    )


def new_draft(client: TestClient, professor: int) -> dict:
    response = client.post("/api/drafts", json={"faculty_id": professor})
    assert response.status_code == 200, response.text
    return response.json()


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/api/drafts"),
        ("post", "/api/drafts"),
        ("get", "/api/drafts/1"),
        ("put", "/api/drafts/1"),
        ("post", "/api/drafts/1/approve"),
        ("delete", "/api/drafts/1/approval"),
    ],
)
def test_draft_routes_require_a_session(client: TestClient, method: str, path: str) -> None:
    kwargs = {"json": {}} if method in ("post", "put") else {}

    response = getattr(client, method)(path, **kwargs)

    assert response.status_code == 401


def test_generate_lint_approve_and_list(client: TestClient, settings: Settings) -> None:
    professor = seed_professor(settings)
    student(client)

    draft = new_draft(client, professor)
    approved = client.post(f"/api/drafts/{draft['id']}/approve", json={"version_no": 1})
    listing = client.get("/api/drafts").json()["drafts"]

    assert draft["lint"] == []
    assert draft["current"]["recipient"] == "jane.doe@cs.example.edu"
    assert draft["current"]["evidence"]
    assert approved.status_code == 200, approved.text
    assert approved.json()["approval"]["version_no"] == 1
    assert listing == [
        {
            "id": draft["id"],
            "target_id": professor,
            "target_name": "Jane Doe",
            "subject": draft["current"]["subject"],
            "version_no": 1,
            "recipient": "jane.doe@cs.example.edu",
            "flag_count": 0,
            "approved": True,
            "updated_at": listing[0]["updated_at"],
        }
    ]
    assert "history" not in listing[0]


def test_an_edit_clears_the_approval_and_a_stale_version_cannot_be_approved(
    client: TestClient, settings: Settings
) -> None:
    professor = seed_professor(settings)
    student(client)
    draft = new_draft(client, professor)
    client.post(f"/api/drafts/{draft['id']}/approve", json={"version_no": 1})

    edited = client.put(
        f"/api/drafts/{draft['id']}",
        json={
            "subject": draft["current"]["subject"],
            "body": draft["current"]["body"] + "\n\nP.S. Thanks.",
        },
    )
    stale = client.post(f"/api/drafts/{draft['id']}/approve", json={"version_no": 1})
    fresh = client.post(f"/api/drafts/{draft['id']}/approve", json={"version_no": 2})

    assert edited.json()["approval"] is None
    assert stale.status_code == 409
    assert error_code(stale) == "draft_changed"
    assert fresh.json()["approval"]["version_no"] == 2


def test_the_database_clears_an_approval_on_any_content_change(
    client: TestClient, settings: Settings
) -> None:
    professor = seed_professor(settings)
    student(client)
    draft = new_draft(client, professor)
    client.post(f"/api/drafts/{draft['id']}/approve", json={"version_no": 1})

    with db.session(settings.database_path) as connection:
        connection.execute(
            "UPDATE draft_versions SET recipient = 'other@cs.example.edu' WHERE draft_id = ?",
            (draft["id"],),
        )
        remaining = connection.execute("SELECT COUNT(*) FROM email_approvals").fetchone()[0]

    assert remaining == 0
    assert client.get(f"/api/drafts/{draft['id']}").json()["approval"] is None


def test_deleting_the_attached_cv_clears_the_approval(
    client: TestClient, settings: Settings
) -> None:
    professor = seed_professor(settings)
    student(client)
    draft = new_draft(client, professor)
    client.post(f"/api/drafts/{draft['id']}/approve", json={"version_no": 1})
    document = client.get("/api/documents").json()["documents"][0]["id"]

    assert client.delete(f"/api/documents/{document}").status_code == 204

    after = client.get(f"/api/drafts/{draft['id']}").json()
    assert after["current"]["cv_version_id"] is None
    assert after["approval"] is None


def test_blocking_lint_and_an_unverified_recipient_refuse_approval(
    client: TestClient, settings: Settings
) -> None:
    professor = seed_professor(settings)
    student(client)
    draft = new_draft(client, professor)
    client.put(
        f"/api/drafts/{draft['id']}",
        json={"subject": "RE: INTERNSHIP", "body": "Click here: https://bit.ly/x"},
    )

    blocked = client.post(f"/api/drafts/{draft['id']}/approve", json={"version_no": 2})

    assert blocked.status_code == 409
    assert error_code(blocked) == "draft_lint_blocked"
    message = blocked.json()["error"]["message"]
    for code in ("subject_reply_prefix", "spam_phrase", "url_shortener"):
        assert code in message
    assert "Click here" not in message

    with db.session(settings.database_path) as connection:
        connection.execute(
            "UPDATE faculty_contacts SET status = 'unverified' WHERE faculty_id = ?", (professor,)
        )
    # The draft still names the old address, but it is no longer verified:
    # approval re-checks the recipient before anything else (QA n3 on #51).
    unverified = client.post(f"/api/drafts/{draft['id']}/approve", json={"version_no": 2})
    assert unverified.status_code == 409
    assert error_code(unverified) == "draft_recipient_unverified"


def test_an_approval_can_be_withdrawn(client: TestClient, settings: Settings) -> None:
    professor = seed_professor(settings)
    student(client)
    draft = new_draft(client, professor)
    client.post(f"/api/drafts/{draft['id']}/approve", json={"version_no": 1})

    withdrawn = client.delete(f"/api/drafts/{draft['id']}/approval")
    again = client.delete(f"/api/drafts/{draft['id']}/approval")

    assert withdrawn.json()["approval"] is None
    assert again.status_code == 200


def test_bodies_are_validated_and_drafts_are_private(
    settings: Settings, client: TestClient
) -> None:
    professor = seed_professor(settings)
    student(client)
    draft = new_draft(client, professor)

    for body in ({"subject": "x" * 201, "body": "b"}, {"subject": "s", "body": "b", "extra": 1}):
        response = client.put(f"/api/drafts/{draft['id']}", json=body)
        assert response.status_code == 422
    assert (
        client.post(f"/api/drafts/{draft['id']}/approve", json={"version_no": 0}).status_code == 422
    )

    with TestClient(create_app(settings)) as other:
        student(other, email="ben@example.edu")
        assert other.get("/api/drafts").json()["drafts"] == []
        missing = other.get(f"/api/drafts/{draft['id']}")
        assert missing.status_code == 404
        assert error_code(missing) == "draft_missing"
        assert (
            other.post(f"/api/drafts/{draft['id']}/approve", json={"version_no": 1}).status_code
            == 404
        )


def test_draft_writes_are_rate_limited(
    client: TestClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    professor = seed_professor(settings)
    student(client)
    monkeypatch.setattr(
        ratelimit,
        "DRAFT_RULE",
        ratelimit.RateLimitRule(name="draft.write", limit=1, window_seconds=300),
    )
    draft = new_draft(client, professor)

    refused = client.post(f"/api/drafts/{draft['id']}/approve", json={"version_no": 1})
    reading = client.get(f"/api/drafts/{draft['id']}")

    assert refused.status_code == 429
    assert reading.status_code == 200


def test_the_approval_audit_records_ids_and_hashes_only(
    client: TestClient, settings: Settings
) -> None:
    professor = seed_professor(settings)
    student(client)
    draft = new_draft(client, professor)
    client.post(f"/api/drafts/{draft['id']}/approve", json={"version_no": 1})

    with db.session(settings.database_path) as connection:
        row = connection.execute(
            "SELECT metadata_json FROM audit_events WHERE action = 'draft.approved'"
        ).fetchone()
    metadata = json.loads(row["metadata_json"])
    assert set(metadata) == {"version_no", "content_sha256"}
    assert "Protein" not in row["metadata_json"]
