"""HTTP content API: profile, document upload/parse and the fact workflow.

Everything runs against a temporary database through ``fastapi.testclient``, so
no test binds a port, opens a browser or touches the network. The service
modules already have their own unit tests; these check the HTTP contract: auth,
ownership, multipart handling, status mapping and the error envelope.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pymupdf
import pytest
from fastapi.testclient import TestClient
from httpx import Response

from research_inter_bridge import db
from research_inter_bridge import documents as document_service
from research_inter_bridge.api import create_app
from research_inter_bridge.config import Settings

EMAIL = "student@example.edu"
OTHER_EMAIL = "second@example.edu"
PASSWORD = "correct horse battery staple"
CONSENTS = ["personal_data", "document_processing"]
ORIGIN = "http://localhost:3000"

CV_TEXT = "\n".join(
    [
        "Asha Verma",
        "Contact: asha.verma@example.edu",
        "Phone: +91 98765 43210",
        "CGPA: 8.9/10",
        "Batch of 2027",
    ]
)


def make_settings(tmp_path: Path) -> Settings:
    return Settings(
        database_path=tmp_path / "api.db",
        data_directory=tmp_path / "data",
        cors_origins=(ORIGIN,),
    )


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return make_settings(tmp_path)


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    # The context manager runs the lifespan, which applies migrations exactly as
    # `serve` does at startup.
    with TestClient(create_app(settings)) as test_client:
        yield test_client


def new_client(settings: Settings) -> TestClient:
    """A second browser: its own cookie jar, so ownership can be tested."""

    return TestClient(create_app(settings))


def register(client: TestClient, *, email: str = EMAIL) -> Response:
    return client.post(
        "/api/auth/register",
        json={"email": email, "password": PASSWORD, "consents": CONSENTS},
    )


def error_code(response: Response) -> str:
    return str(response.json()["error"]["code"])


def profile_body(**overrides: object) -> dict[str, object]:
    body: dict[str, object] = {
        "full_name": "Asha Verma",
        "contact_email": "asha@example.edu",
        "institution": "Example Institute of Technology",
        "department": "Computer Science",
        "country": "India",
        "degree_type": "btech",
        "current_year": 3,
        "expected_graduation": "2027-05-31",
        "skills": ["Python", "PyTorch"],
        "research_keywords": ["machine learning", "computer vision"],
    }
    body.update(overrides)
    return body


def upload(
    client: TestClient,
    payload: bytes = CV_TEXT.encode(),
    *,
    name: str = "cv.txt",
    kind: str = "cv",
    content_type: str = "text/plain",
) -> Response:
    return client.post(
        "/api/documents",
        files={"file": (name, payload, content_type)},
        data={"kind": kind},
    )


def upload_and_parse(client: TestClient, payload: bytes = CV_TEXT.encode()) -> dict:
    uploaded = upload(client, payload)
    assert uploaded.status_code == 201, uploaded.text
    parsed = client.post(f"/api/documents/{uploaded.json()['id']}/parse")
    assert parsed.status_code == 200, parsed.text
    return parsed.json()


def blank_pdf() -> bytes:
    document = pymupdf.open()
    document.new_page()
    payload = document.tobytes()
    document.close()
    return payload


# --------------------------------------------------------------------------- #
# authentication and ownership
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        ("GET", "/api/profile", None),
        ("PUT", "/api/profile", {"full_name": "Asha", "contact_email": "a@example.edu"}),
        ("GET", "/api/documents", None),
        ("POST", "/api/documents", None),
        ("POST", "/api/documents/1/parse", None),
        ("DELETE", "/api/documents/1", None),
        ("GET", "/api/facts", None),
        ("PATCH", "/api/facts/1", {"status": "confirmed"}),
        ("POST", "/api/facts/apply", {}),
    ],
)
def test_every_content_route_requires_a_session(
    client: TestClient, method: str, path: str, body: dict[str, object] | None
) -> None:
    response = client.request(method, path, json=body)

    assert response.status_code == 401
    assert error_code(response) == "invalid_session"


def test_one_student_cannot_see_another_students_profile(
    client: TestClient, settings: Settings
) -> None:
    assert register(client).status_code == 201
    assert client.put("/api/profile", json=profile_body()).status_code == 200

    other = new_client(settings)
    try:
        assert register(other, email=OTHER_EMAIL).status_code == 201
        missing = other.get("/api/profile")
        assert missing.status_code == 404
        assert error_code(missing) == "profile_missing"
    finally:
        other.close()

    # The first student's row is untouched by the second student's request.
    assert client.get("/api/profile").json()["full_name"] == "Asha Verma"


# --------------------------------------------------------------------------- #
# profile
# --------------------------------------------------------------------------- #


def test_profile_is_created_read_back_and_reports_completeness(client: TestClient) -> None:
    assert register(client).status_code == 201
    assert client.get("/api/profile").status_code == 404

    saved = client.put("/api/profile", json=profile_body())

    assert saved.status_code == 200
    body = saved.json()
    assert body["user_id"] >= 1
    assert body["full_name"] == "Asha Verma"
    assert body["degree_type"] == "btech"
    assert body["expected_graduation"] == "2027-05-31"
    assert body["skills"] == ["Python", "PyTorch"]
    assert body["completeness_percent"] > 0
    assert "CGPA/GPA" in body["completeness_missing"]
    assert body["created_at"] and body["updated_at"]

    again = client.get("/api/profile")
    assert again.status_code == 200
    assert again.json() == body
    assert again.headers["cache-control"] == "no-store"


def test_profile_put_replaces_omitted_optional_fields(client: TestClient) -> None:
    assert register(client).status_code == 201
    assert client.put("/api/profile", json=profile_body(phone="+91 98765 43210")).status_code == 200

    replaced = client.put("/api/profile", json=profile_body())

    assert replaced.status_code == 200
    assert replaced.json()["phone"] is None
    assert replaced.json()["institution"] == "Example Institute of Technology"


def test_profile_consent_timestamp_comes_from_the_ledger(client: TestClient) -> None:
    registered = register(client)
    assert registered.status_code == 201

    saved = client.put("/api/profile", json=profile_body()).json()

    # The registration path recorded document-processing consent, and the service
    # copied that ledger timestamp rather than trusting a payload value.
    assert saved["consent_given_at"]

    refused = client.put("/api/profile", json=profile_body(consent_given_at="2000-01-01T00:00:00Z"))
    assert refused.status_code == 422
    assert error_code(refused) == "invalid_request"
    assert "consent_given_at" in refused.text


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"gpa": 11.0, "gpa_scale": 10.0}, "gpa"),
        ({"gpa": -1.0}, "gpa"),
        ({"degree_type": "diploma"}, "degree_type"),
        ({"expected_graduation": "May 2027"}, "expected_graduation"),
        ({"internship_start": "2027-06-01", "internship_end": "2027-05-01"}, "internship_start"),
        ({"full_name": "   "}, "full_name"),
        ({"contact_email": "not-an-email"}, "contact_email"),
    ],
)
def test_invalid_profile_values_are_rejected_with_an_actionable_message(
    client: TestClient, overrides: dict[str, object], fragment: str
) -> None:
    assert register(client).status_code == 201

    response = client.put("/api/profile", json=profile_body(**overrides))

    assert response.status_code == 400
    assert error_code(response) == "invalid_profile"
    assert fragment in response.text


def test_profile_ownership_fields_cannot_be_supplied_by_the_client(client: TestClient) -> None:
    assert register(client).status_code == 201

    response = client.put("/api/profile", json=profile_body(user_id=999))

    assert response.status_code == 422
    assert error_code(response) == "invalid_request"
    assert "user_id" in response.text
    assert client.get("/api/profile").status_code == 404


def test_profile_rejects_non_finite_numbers(client: TestClient) -> None:
    assert register(client).status_code == 201

    # httpx refuses to serialize NaN, so the body is written by hand: a client
    # can still send it, and Python's json module accepts the literal.
    response = client.put(
        "/api/profile",
        content='{"full_name": "Asha", "contact_email": "asha@example.edu", "gpa": NaN}',
        headers={"Content-Type": "application/json"},
    )

    assert response.status_code == 422
    assert error_code(response) == "invalid_request"
    assert client.get("/api/profile").status_code == 404


# --------------------------------------------------------------------------- #
# documents
# --------------------------------------------------------------------------- #


def test_upload_is_refused_without_document_processing_consent(
    client: TestClient, settings: Settings
) -> None:
    registered = register(client)
    user_id = registered.json()["account"]["user_id"]
    with db.session(settings.database_path) as connection:
        connection.execute(
            "DELETE FROM consents WHERE user_id = ? AND kind = 'document_processing'",
            (user_id,),
        )

    response = upload(client)

    assert response.status_code == 403
    assert error_code(response) == "consent_required"
    assert client.get("/api/documents").json()["documents"] == []


def test_upload_lists_and_dedupes_identical_bytes(client: TestClient) -> None:
    assert register(client).status_code == 201

    first = upload(client)
    second = upload(client, name="same-cv.txt")

    assert first.status_code == 201
    assert second.status_code == 201
    assert first.json()["id"] == second.json()["id"]

    listing = client.get("/api/documents").json()["documents"]
    assert len(listing) == 1
    assert listing[0]["display_name"] == "cv.txt"
    assert listing[0]["status"] == "stored"
    # The record never exposes the on-disk path or the owner id.
    assert set(listing[0]) == {
        "id",
        "kind",
        "display_name",
        "media_type",
        "size_bytes",
        "status",
        "sha256",
        "uploaded_at",
        "parsed_at",
        "parse_error",
    }


@pytest.mark.parametrize(
    ("name", "payload", "content_type", "status_code", "code"),
    [
        ("cv.exe", b"MZ\x90\x00\x03", "application/octet-stream", 415, "unsupported_document"),
        ("cv.pdf", b"MZ\x90\x00\x03", "application/pdf", 415, "unsupported_document"),
        ("cv.pdf", b"just plain text", "application/pdf", 415, "unsupported_document"),
        ("cv.txt", b"", "text/plain", 415, "unsupported_document"),
        ("cv.txt", b"plain text", "text/html", 415, "unsupported_document"),
    ],
)
def test_upload_rejects_files_it_cannot_use(
    client: TestClient,
    name: str,
    payload: bytes,
    content_type: str,
    status_code: int,
    code: str,
) -> None:
    assert register(client).status_code == 201

    response = upload(client, payload, name=name, content_type=content_type)

    assert response.status_code == status_code
    assert error_code(response) == code
    assert client.get("/api/documents").json()["documents"] == []


def test_oversized_upload_is_refused_before_it_is_read_into_memory(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert register(client).status_code == 201
    monkeypatch.setattr(document_service, "MAX_DOCUMENT_BYTES", 8)

    response = upload(client, b"more than eight bytes")

    assert response.status_code == 413
    assert error_code(response) == "document_too_large"
    assert "limit" in response.text


def test_parse_extracts_pending_facts_and_is_idempotent(client: TestClient) -> None:
    assert register(client).status_code == 201

    first = upload_and_parse(client)

    assert first["document"]["status"] == "parsed"
    assert first["character_count"] >= len(CV_TEXT)
    assert first["fact_count"] >= 3

    second = client.post(f"/api/documents/{first['document']['id']}/parse")
    assert second.status_code == 200
    assert second.json()["fact_count"] == first["fact_count"]

    groups = client.get("/api/facts").json()["groups"]
    fields = {group["field"] for group in groups}
    assert {"contact_email", "gpa", "phone"} <= fields
    assert all(
        candidate["status"] == "pending"
        for group in groups
        for candidate in (*group["pending"], *group["confirmed"])
    )


def test_a_blank_pdf_is_stored_but_reported_as_unreadable(client: TestClient) -> None:
    assert register(client).status_code == 201
    uploaded = upload(client, blank_pdf(), name="scan.pdf", content_type="application/pdf")
    assert uploaded.status_code == 201

    parsed = client.post(f"/api/documents/{uploaded.json()['id']}/parse")

    assert parsed.status_code == 422
    assert error_code(parsed) == "document_unreadable"
    # The failure is persisted so the UI can explain it later.
    stored = client.get(f"/api/documents/{uploaded.json()['id']}").json()
    assert stored["status"] == "failed"
    assert stored["parse_error"]


def test_document_routes_are_owner_scoped(client: TestClient, settings: Settings) -> None:
    assert register(client).status_code == 201
    uploaded = upload(client).json()

    other = new_client(settings)
    try:
        assert register(other, email=OTHER_EMAIL).status_code == 201
        assert other.get("/api/documents").json()["documents"] == []
        assert other.get(f"/api/documents/{uploaded['id']}").status_code == 404
        assert other.post(f"/api/documents/{uploaded['id']}/parse").status_code == 404
        assert other.delete(f"/api/documents/{uploaded['id']}").status_code == 404
    finally:
        other.close()

    assert client.get(f"/api/documents/{uploaded['id']}").status_code == 200


def test_delete_removes_the_row_and_the_file(client: TestClient, settings: Settings) -> None:
    assert register(client).status_code == 201
    uploaded = upload(client).json()
    with db.session(settings.database_path) as connection:
        record = document_service.get_document(
            connection,
            document_id=uploaded["id"],
            user_id=client.get("/api/auth/me").json()["account"]["user_id"],
        )
    stored_file = settings.data_directory / "documents" / record.storage_path
    assert stored_file.is_file()

    removed = client.delete(f"/api/documents/{uploaded['id']}")

    assert removed.status_code == 204
    assert client.get("/api/documents").json()["documents"] == []
    assert not stored_file.exists()
    assert client.delete(f"/api/documents/{uploaded['id']}").status_code == 404


def test_a_locked_file_keeps_the_document_and_can_be_retried(
    client: TestClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert register(client).status_code == 201
    uploaded = upload(client).json()
    with db.session(settings.database_path) as connection:
        record = document_service.get_document(
            connection,
            document_id=uploaded["id"],
            user_id=client.get("/api/auth/me").json()["account"]["user_id"],
        )
    stored_file = settings.data_directory / "documents" / record.storage_path
    assert stored_file.is_file()

    original_unlink = Path.unlink

    def locked_unlink(self: Path, *args: object, **kwargs: object) -> None:
        if self == stored_file:
            raise PermissionError("the file is open in another program")
        original_unlink(self, *args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(Path, "unlink", locked_unlink)

    refused = client.delete(f"/api/documents/{uploaded['id']}")

    assert refused.status_code == 409
    assert error_code(refused) == "deletion_incomplete"
    # The row is still there, so the student can close the program and retry;
    # deleting the row first would have orphaned an unreferenced file.
    assert [item["id"] for item in client.get("/api/documents").json()["documents"]] == [
        uploaded["id"]
    ]
    assert stored_file.exists()

    monkeypatch.undo()
    retried = client.delete(f"/api/documents/{uploaded['id']}")

    assert retried.status_code == 204
    assert not stored_file.exists()


# --------------------------------------------------------------------------- #
# facts
# --------------------------------------------------------------------------- #


def test_confirming_a_fact_records_the_choice_and_apply_fills_blank_profile_fields(
    client: TestClient,
) -> None:
    assert register(client).status_code == 201
    assert client.put("/api/profile", json=profile_body()).status_code == 200
    upload_and_parse(client)

    groups = {group["field"]: group for group in client.get("/api/facts").json()["groups"]}
    gpa_fact = groups["gpa"]["pending"][0]

    confirmed = client.patch(f"/api/facts/{gpa_fact['id']}", json={"status": "confirmed"})

    assert confirmed.status_code == 200
    assert confirmed.json() == {
        "fact_id": gpa_fact["id"],
        "status": "confirmed",
        "already_set": False,
    }
    repeated = client.patch(f"/api/facts/{gpa_fact['id']}", json={"status": "confirmed"})
    assert repeated.json()["already_set"] is True

    phone_fact = groups["phone"]["pending"][0]
    assert (
        client.patch(f"/api/facts/{phone_fact['id']}", json={"status": "confirmed"}).status_code
        == 200
    )

    applied = client.post("/api/facts/apply", json={})

    assert applied.status_code == 200
    assert "gpa" in applied.json()["applied"]
    profile = client.get("/api/profile").json()
    assert profile["gpa"] == 8.9
    assert profile["gpa_scale"] == 10.0
    assert profile["phone"] == "+91 98765 43210"
    # A value the student typed is never replaced by a fact with the same field.
    assert profile["contact_email"] == "asha@example.edu"


def test_apply_only_fills_blank_fields_unless_overwrite_is_requested(
    client: TestClient,
) -> None:
    assert register(client).status_code == 201
    assert client.put("/api/profile", json=profile_body(phone="+44 7700 900123")).status_code == 200
    upload_and_parse(client)
    groups = {group["field"]: group for group in client.get("/api/facts").json()["groups"]}
    phone_fact = groups["phone"]["pending"][0]
    assert (
        client.patch(f"/api/facts/{phone_fact['id']}", json={"status": "confirmed"}).status_code
        == 200
    )

    kept = client.post("/api/facts/apply", json={"fields": ["phone"]})

    assert kept.status_code == 200
    assert kept.json()["applied"] == []
    assert client.get("/api/profile").json()["phone"] == "+44 7700 900123"

    replaced = client.post("/api/facts/apply", json={"fields": ["phone"], "overwrite": True})

    assert replaced.status_code == 200
    assert replaced.json()["applied"] == ["phone"]
    assert client.get("/api/profile").json()["phone"] == "+91 98765 43210"


def test_apply_refuses_an_unknown_field_name(client: TestClient) -> None:
    assert register(client).status_code == 201
    assert client.put("/api/profile", json=profile_body()).status_code == 200
    upload_and_parse(client)

    response = client.post("/api/facts/apply", json={"fields": ["gpa_typo"]})

    assert response.status_code == 400
    assert error_code(response) == "invalid_profile"
    assert "applicable fields" in response.text


def test_confirming_a_corrected_value_is_validated(client: TestClient) -> None:
    assert register(client).status_code == 201
    upload_and_parse(client)
    groups = {group["field"]: group for group in client.get("/api/facts").json()["groups"]}
    gpa_fact = groups["gpa"]["pending"][0]

    blank = client.patch(
        f"/api/facts/{gpa_fact['id']}",
        json={"status": "confirmed", "corrected_value": "   "},
    )
    assert blank.status_code == 400
    assert error_code(blank) == "invalid_profile"

    corrected = client.patch(
        f"/api/facts/{gpa_fact['id']}",
        json={"status": "confirmed", "corrected_value": "9.1/10"},
    )
    assert corrected.status_code == 200

    # Editing an already-confirmed fact must apply the new correction, not answer
    # 200 and silently keep the old value.
    edited = client.patch(
        f"/api/facts/{gpa_fact['id']}",
        json={"status": "confirmed", "corrected_value": "9.4/10"},
    )
    assert edited.status_code == 200
    assert edited.json()["already_set"] is True
    groups = client.get("/api/facts").json()["groups"]
    group = next(group for group in groups if group["field"] == "gpa")
    assert [candidate["value"] for candidate in group["confirmed"]] == ["9.4/10"]
    assert group["pending"] == []

    # Re-parsing the same document must not bring the original 8.9/10 back as a
    # new pending candidate.
    document_id = client.get("/api/documents").json()["documents"][0]["id"]
    assert client.post(f"/api/documents/{document_id}/parse").status_code == 200
    groups = client.get("/api/facts").json()["groups"]
    group = next(group for group in groups if group["field"] == "gpa")
    assert group["pending"] == []
    assert [candidate["value"] for candidate in group["confirmed"]] == ["9.4/10"]


def test_confirming_a_previously_rejected_candidate_moves_the_single_truth(
    client: TestClient,
) -> None:
    assert register(client).status_code == 201
    assert upload_and_parse(client)["fact_count"] > 0
    other_text = CV_TEXT.replace("98765 43210", "98765 11111").encode()
    assert upload_and_parse(client, other_text)["fact_count"] > 0

    groups = {group["field"]: group for group in client.get("/api/facts").json()["groups"]}
    first, second = groups["phone"]["pending"]
    assert first["value"] != second["value"]

    assert (
        client.patch(f"/api/facts/{first['id']}", json={"status": "confirmed"}).status_code == 200
    )
    # `second` is now rejected. Confirming it explicitly must leave one truth.
    moved = client.patch(f"/api/facts/{second['id']}", json={"status": "confirmed"})

    assert moved.status_code == 200
    group = next(
        group for group in client.get("/api/facts").json()["groups"] if group["field"] == "phone"
    )
    assert [candidate["value"] for candidate in group["confirmed"]] == [second["value"]]
    assert group["pending"] == []
    assert group["has_conflict"] is False


def test_rejecting_a_fact_hides_it_from_the_candidate_list(client: TestClient) -> None:
    assert register(client).status_code == 201
    upload_and_parse(client)
    groups = {group["field"]: group for group in client.get("/api/facts").json()["groups"]}
    fact_id = groups["phone"]["pending"][0]["id"]

    rejected = client.patch(f"/api/facts/{fact_id}", json={"status": "rejected"})

    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    remaining = client.get("/api/facts").json()["groups"]
    assert "phone" not in {group["field"] for group in remaining}


def test_a_confirmed_fact_cannot_be_rejected(client: TestClient) -> None:
    assert register(client).status_code == 201
    upload_and_parse(client)
    groups = {group["field"]: group for group in client.get("/api/facts").json()["groups"]}
    fact_id = groups["gpa"]["pending"][0]["id"]
    assert client.patch(f"/api/facts/{fact_id}", json={"status": "confirmed"}).status_code == 200

    refused = client.patch(f"/api/facts/{fact_id}", json={"status": "rejected"})

    assert refused.status_code == 400
    assert error_code(refused) == "invalid_profile"
    assert "confirmed fact" in refused.text


@pytest.mark.parametrize(
    ("status", "extra", "status_code", "code"),
    [
        ("maybe", {}, 422, "invalid_request"),
        ("rejected", {"corrected_value": "8.9"}, 400, "invalid_profile"),
    ],
)
def test_fact_update_rejects_an_unsupported_status_or_correction(
    client: TestClient,
    status: str,
    extra: dict[str, str],
    status_code: int,
    code: str,
) -> None:
    assert register(client).status_code == 201
    upload_and_parse(client)
    fact_id = client.get("/api/facts").json()["groups"][0]["pending"][0]["id"]

    response = client.patch(f"/api/facts/{fact_id}", json={"status": status, **extra})

    assert response.status_code == status_code
    assert error_code(response) == code


def test_unknown_and_foreign_facts_answer_404(client: TestClient, settings: Settings) -> None:
    assert register(client).status_code == 201
    upload_and_parse(client)
    fact_id = client.get("/api/facts").json()["groups"][0]["pending"][0]["id"]

    missing = client.patch("/api/facts/999999", json={"status": "confirmed"})
    assert missing.status_code == 404
    assert error_code(missing) == "fact_missing"

    other = new_client(settings)
    try:
        assert register(other, email=OTHER_EMAIL).status_code == 201
        foreign = other.patch(f"/api/facts/{fact_id}", json={"status": "confirmed"})
        assert foreign.status_code == 404
        assert error_code(foreign) == "fact_missing"
    finally:
        other.close()


# --------------------------------------------------------------------------- #
# browser-facing contract
# --------------------------------------------------------------------------- #


def test_content_responses_are_not_cacheable(client: TestClient) -> None:
    assert register(client).status_code == 201
    assert client.put("/api/profile", json=profile_body()).status_code == 200
    upload(client)

    assert client.get("/api/profile").headers["cache-control"] == "no-store"
    assert client.get("/api/documents").headers["cache-control"] == "no-store"
    assert client.get("/api/facts").headers["cache-control"] == "no-store"


def test_cors_preflight_covers_the_new_methods(client: TestClient) -> None:
    allowed = client.options(
        "/api/profile",
        headers={
            "Origin": ORIGIN,
            "Access-Control-Request-Method": "PUT",
            "Access-Control-Request-Headers": "content-type",
        },
    )
    patched = client.options(
        "/api/facts/1",
        headers={
            "Origin": ORIGIN,
            "Access-Control-Request-Method": "PATCH",
            "Access-Control-Request-Headers": "content-type",
        },
    )

    assert allowed.status_code == 200
    assert "PUT" in allowed.headers["access-control-allow-methods"]
    assert patched.status_code == 200
    assert "PATCH" in patched.headers["access-control-allow-methods"]


def test_cross_origin_writes_are_blocked_before_authentication(client: TestClient) -> None:
    for method, path, body in [
        ("PUT", "/api/profile", {"full_name": "Asha", "contact_email": "a@example.edu"}),
        ("PATCH", "/api/facts/1", {"status": "confirmed"}),
        ("DELETE", "/api/documents/1", None),
    ]:
        response = client.request(
            method,
            path,
            json=body,
            headers={"Origin": "https://evil.example"},
        )

        assert response.status_code == 403
        assert error_code(response) == "origin_not_allowed"
