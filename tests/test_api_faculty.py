"""HTTP contract for the cache-only faculty browse endpoints (Phase 5d-2).

The service rules are covered in ``tests/test_faculty.py``. These tests check the
HTTP layer: a session is required, filters and paging arrive as query params,
an unknown id is ``404 faculty_missing``, only a verified address is ever
serialised (ADR-021), and the institution/department browse rows carry
``faculty_count``. No test opens a socket: the routes never call a source.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from httpx import Response

from research_inter_bridge import db
from research_inter_bridge import faculty as fac
from research_inter_bridge.api import create_app
from research_inter_bridge.config import Settings

EMAIL = "student@example.edu"
PASSWORD = "correct horse battery staple"
CONSENTS = ["personal_data", "document_processing"]
ORIGIN = "http://localhost:3000"

JANE = "https://www.cs.example.edu/people/jane.doe"
RAVI = "https://www.cs.example.edu/people/ravi"
ADA = "https://www.bio.example.edu/people/ada"
UNVERIFIED_ADDRESS = "ravi.private@cs.example.edu"


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(
        database_path=tmp_path / "api.db",
        data_directory=tmp_path / "data",
        cors_origins=(ORIGIN,),
    )


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@dataclass
class FakeAdapter:
    records: tuple[fac.DiscoveredFaculty, ...]
    name: str = "fixture"
    parser_version: str = "1"
    contact_authority: str = "official"

    def search(self, query: fac.FacultyQuery) -> tuple[fac.DiscoveredFaculty, ...]:
        return self.records


def person(**overrides: object) -> fac.DiscoveredFaculty:
    base: dict[str, object] = {
        "full_name": "Jane Doe",
        "institution": "Example University",
        "country": "Germany",
        "department": "Computer Science",
        "profile_url": JANE,
        "email": "jane.doe@cs.example.edu",
        "email_source_url": JANE,
        "research_interests": ("machine learning",),
        "publications": (
            fac.DiscoveredPublication(
                title="Graph Learning",
                doi="10.1234/g",
                year=2025,
                abstract="We learn on graphs.",
            ),
            fac.DiscoveredPublication(title="Older Work", doi="10.1234/o", year=2015),
        ),
    }
    base.update(overrides)
    return fac.DiscoveredFaculty(**base)  # type: ignore[arg-type]


PEOPLE = (
    person(),
    person(
        full_name="Ravi Kumar",
        profile_url=RAVI,
        email=UNVERIFIED_ADDRESS,
        email_source_url="https://ravi.example.org/",
        research_interests=("databases",),
        publications=(),
    ),
    person(
        full_name="Ada Byron",
        institution="Delft Example Institute",
        department="Biology",
        country="Netherlands",
        profile_url=ADA,
        email=None,
        email_source_url=None,
        research_interests=("genomics",),
        publications=(),
    ),
)


def seed(settings: Settings, *records: fac.DiscoveredFaculty, now: datetime | None = None) -> None:
    with db.session(settings.database_path) as connection:
        db.apply_migrations(connection)
        fac.search_faculty(
            connection,
            fac.FacultyQuery(),
            adapter=FakeAdapter(records=records or PEOPLE),
            # Wall-clock seed: the API judges staleness with the real clock.
            now=now or datetime.now(UTC),
        )


def register(client: TestClient) -> Response:
    return client.post(
        "/api/auth/register",
        json={"email": EMAIL, "password": PASSWORD, "consents": CONSENTS},
    )


def error_code(response: Response) -> str:
    return str(response.json()["error"]["code"])


def by_name(response: Response) -> dict[str, dict]:
    return {member["full_name"]: member for member in response.json()["faculty"]}


@pytest.mark.parametrize("path", ["/api/research/faculty", "/api/research/faculty/1"])
def test_faculty_routes_require_a_session(client: TestClient, path: str) -> None:
    response = client.get(path)

    assert response.status_code == 401
    assert error_code(response) == "invalid_session"


def test_faculty_list_paginates_sorted_by_name(client: TestClient, settings: Settings) -> None:
    register(client)
    seed(settings)

    first = client.get("/api/research/faculty", params={"limit": 2})
    second = client.get("/api/research/faculty", params={"limit": 2, "offset": 2})

    assert first.status_code == 200
    body = first.json()
    assert (body["total"], body["limit"], body["offset"]) == (3, 2, 0)
    assert [m["full_name"] for m in body["faculty"]] == ["Ada Byron", "Jane Doe"]
    assert [m["full_name"] for m in second.json()["faculty"]] == ["Ravi Kumar"]
    assert first.headers["cache-control"] == "no-store"


def test_faculty_list_filters(client: TestClient, settings: Settings) -> None:
    register(client)
    seed(settings)
    jane = by_name(client.get("/api/research/faculty"))["Jane Doe"]

    def names(**params: object) -> list[str]:
        response = client.get("/api/research/faculty", params=params)
        assert response.status_code == 200
        return [m["full_name"] for m in response.json()["faculty"]]

    assert names(area="machine") == ["Jane Doe"]
    assert names(country="netherlands") == ["Ada Byron"]
    assert names(institution_id=jane["institution_id"]) == ["Jane Doe", "Ravi Kumar"]
    assert names(department_id=jane["department_id"]) == ["Jane Doe", "Ravi Kumar"]
    assert names(institution_id=4242) == []


def test_only_a_verified_address_is_serialised(client: TestClient, settings: Settings) -> None:
    register(client)
    seed(settings)

    listing = client.get("/api/research/faculty")
    people = by_name(listing)
    ravi_detail = client.get(f"/api/research/faculty/{people['Ravi Kumar']['id']}")

    assert people["Jane Doe"]["email"] == "jane.doe@cs.example.edu"
    assert people["Jane Doe"]["email_status"] == "verified"
    assert people["Jane Doe"]["contact_sendable"] is True
    assert people["Ravi Kumar"]["email"] is None
    assert people["Ravi Kumar"]["email_status"] == "unverified"
    assert people["Ravi Kumar"]["email_source_url"] == "https://ravi.example.org/"
    assert people["Ravi Kumar"]["contact_sendable"] is False
    assert people["Ada Byron"]["email"] is None
    assert people["Ada Byron"]["email_status"] == "unavailable"
    # The stored unverified value never reaches the wire, list or detail.
    assert UNVERIFIED_ADDRESS not in listing.text
    assert UNVERIFIED_ADDRESS not in ravi_detail.text


def test_a_stale_verified_address_is_shown_but_not_sendable(
    client: TestClient, settings: Settings
) -> None:
    register(client)
    seed(settings, person(), now=datetime(2026, 1, 1, tzinfo=UTC))

    (member,) = client.get("/api/research/faculty").json()["faculty"]

    assert member["stale"] is True
    assert member["email"] == "jane.doe@cs.example.edu"
    assert member["contact_sendable"] is False


def test_list_rows_carry_highlights_and_detail_carries_every_paper(
    client: TestClient, settings: Settings
) -> None:
    register(client)
    seed(settings)
    jane = by_name(client.get("/api/research/faculty"))["Jane Doe"]

    detail = client.get(f"/api/research/faculty/{jane['id']}")

    assert jane["publication_count"] == 2
    assert jane["highlighted_publications"][0]["title"] == "Graph Learning"
    assert "abstract" not in jane["highlighted_publications"][0]
    assert "publications" not in jane
    assert detail.status_code == 200
    body = detail.json()
    assert body["id"] == jane["id"]
    assert [p["title"] for p in body["publications"]] == ["Graph Learning", "Older Work"]
    assert body["publications"][0]["abstract"] == "We learn on graphs."
    assert body["publications"][0]["doi"] == "10.1234/g"
    serialised = detail.text.casefold()
    for forbidden in ("password", "token", "user_id", "raw_payload_path"):
        assert forbidden not in serialised


def test_unknown_faculty_is_404(client: TestClient, settings: Settings) -> None:
    register(client)
    seed(settings)

    response = client.get("/api/research/faculty/9999")

    assert response.status_code == 404
    assert error_code(response) == "faculty_missing"


def test_invalid_faculty_paging_is_a_validation_error(client: TestClient) -> None:
    register(client)

    response = client.get("/api/research/faculty", params={"limit": 0})

    assert response.status_code == 422
    assert error_code(response) == "invalid_request"


def test_browse_rows_carry_faculty_counts(client: TestClient, settings: Settings) -> None:
    register(client)
    seed(settings)

    institutions = client.get("/api/research/institutions").json()["institutions"]
    counts = {item["name"]: item["faculty_count"] for item in institutions}
    example = next(item for item in institutions if item["name"] == "Example University")
    departments = client.get(f"/api/research/institutions/{example['id']}/departments").json()[
        "departments"
    ]

    assert counts == {"Delft Example Institute": 1, "Example University": 2}
    assert example["program_count"] == 0
    assert [(d["name"], d["faculty_count"], d["program_count"]) for d in departments] == [
        ("Computer Science", 2, 0)
    ]
