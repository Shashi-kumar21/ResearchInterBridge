"""HTTP contract for the cache-only research browse endpoints (§13.2).

The service rules are covered in ``tests/test_browse.py``. These tests check the
HTTP layer: a session is required, pagination/filters arrive as query params,
the error envelope maps a missing id to 404, and no response leaks anything a
provider did not state.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from httpx import Response

from research_inter_bridge import db, discovery
from research_inter_bridge.api import create_app
from research_inter_bridge.config import Settings

EMAIL = "student@example.edu"
PASSWORD = "correct horse battery staple"
CONSENTS = ["personal_data", "document_processing"]
ORIGIN = "http://localhost:3000"
NOW = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)


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
    with TestClient(create_app(settings)) as test_client:
        yield test_client


@dataclass
class FakeAdapter:
    records: tuple[discovery.DiscoveredProgram, ...] = ()
    name: str = "fixture"
    parser_version: str = "1"

    def search(self, query: discovery.ProgramQuery) -> tuple[discovery.DiscoveredProgram, ...]:
        return self.records


def make_program(**overrides: object) -> discovery.DiscoveredProgram:
    base: dict[str, object] = {
        "name": "MSc Research Internship",
        "institution": "Technical University of Munich",
        "country": "Germany",
        "department": "Physics",
        "degree_level": "MSc",
        "official_url": "https://www.tum.de/en/studies/programs/msc-research-internship",
        "research_areas": ("machine learning", "physics"),
        "closing_date": "2027-01-15",
        "status": "upcoming",
        "confidence": 0.8,
    }
    base.update(overrides)
    return discovery.DiscoveredProgram(**base)  # type: ignore[arg-type]


TUM = make_program()
DELFT = make_program(
    name="MSc Data Science",
    institution="Delft University of Technology",
    country="Netherlands",
    department="Computer Science",
    research_areas=("data science",),
    official_url="https://www.tudelft.nl/en/education/programmes/masters/data-science",
)
ETH = make_program(
    name="PhD Robotics",
    institution="ETH Zurich",
    country="Switzerland",
    department="Robotics",
    degree_level="PhD",
    research_areas=("robotics",),
    official_url="https://ethz.ch/en/doctorate/robotics",
)


def register(client: TestClient, *, email: str = EMAIL) -> Response:
    return client.post(
        "/api/auth/register",
        json={"email": email, "password": PASSWORD, "consents": CONSENTS},
    )


def seed(settings: Settings) -> None:
    """Fill the shared cache through the normal discovery path."""

    with db.session(settings.database_path) as connection:
        discovery.search_programs(
            connection,
            discovery.ProgramQuery(country="Germany"),
            adapter=FakeAdapter(records=(TUM, DELFT, ETH)),
            now=NOW,
        )


def error_code(response: Response) -> str:
    return str(response.json()["error"]["code"])


# --------------------------------------------------------------------------- #
# authentication
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize(
    "path",
    [
        "/api/research/programs",
        "/api/research/programs/1",
        "/api/research/institutions",
        "/api/research/institutions/1/departments",
    ],
)
def test_research_routes_require_a_session(client: TestClient, path: str) -> None:
    response = client.get(path)

    assert response.status_code == 401
    assert error_code(response) == "invalid_session"


# --------------------------------------------------------------------------- #
# programs
# --------------------------------------------------------------------------- #


def test_programs_endpoint_paginates_filters_and_marks_staleness(
    client: TestClient, settings: Settings
) -> None:
    register(client)
    seed(settings)

    first = client.get("/api/research/programs", params={"limit": 2, "offset": 0})
    second = client.get("/api/research/programs", params={"limit": 2, "offset": 2})
    german = client.get("/api/research/programs", params={"country": "germany"})
    master = client.get("/api/research/programs", params={"degree_level": "MSc"})
    area = client.get("/api/research/programs", params={"area": "data science"})

    assert first.status_code == 200
    body = first.json()
    assert body["total"] == 3
    assert (body["limit"], body["offset"]) == (2, 0)
    assert [program["name"] for program in body["programs"]] == [
        "MSc Data Science",
        "MSc Research Internship",
    ]
    assert second.json()["programs"][0]["name"] == "PhD Robotics"
    assert second.json()["total"] == 3
    assert [program["name"] for program in german.json()["programs"]] == ["MSc Research Internship"]
    # "MSc" is stored as the canonical "master", and the filter is canonicalised
    # the same way, so both master programs match.
    assert [program["name"] for program in master.json()["programs"]] == [
        "MSc Data Science",
        "MSc Research Internship",
    ]
    assert [program["name"] for program in area.json()["programs"]] == ["MSc Data Science"]
    assert first.headers["cache-control"] == "no-store"


def test_a_program_response_carries_only_stated_public_data(
    client: TestClient, settings: Settings
) -> None:
    register(client)
    seed(settings)

    body = client.get("/api/research/programs").json()
    program = body["programs"][0]

    assert program["official_url"]
    assert program["evidence_urls"]
    assert program["last_checked_at"] is not None
    assert program["stale"] is False
    # No secret, token, path or private field may appear in a shared record.
    serialised = str(program).casefold()
    for forbidden in ("password", "token", "user_id", "raw_payload_path"):
        assert forbidden not in serialised


def test_program_detail_and_missing_id(client: TestClient, settings: Settings) -> None:
    register(client)
    seed(settings)
    listed = client.get("/api/research/programs").json()["programs"][0]

    detail = client.get(f"/api/research/programs/{listed['id']}")
    missing = client.get("/api/research/programs/9999")

    assert detail.status_code == 200
    assert detail.json()["id"] == listed["id"]
    assert detail.json()["institution_id"] is not None
    assert missing.status_code == 404
    assert error_code(missing) == "program_missing"


def test_invalid_pagination_is_a_validation_error(client: TestClient) -> None:
    register(client)

    response = client.get("/api/research/programs", params={"limit": 0})

    assert response.status_code == 422
    assert error_code(response) == "invalid_request"


# --------------------------------------------------------------------------- #
# institutions and departments
# --------------------------------------------------------------------------- #


def test_institutions_endpoint_reports_counts_and_country_filter(
    client: TestClient, settings: Settings
) -> None:
    register(client)
    seed(settings)

    response = client.get("/api/research/institutions")
    german = client.get("/api/research/institutions", params={"country": "Germany"})

    assert response.status_code == 200
    body = response.json()
    assert body["total"] == 3
    assert [item["name"] for item in body["institutions"]] == [
        "Delft University of Technology",
        "ETH Zurich",
        "Technical University of Munich",
    ]
    tum = next(item for item in body["institutions"] if "Munich" in item["name"])
    assert (tum["program_count"], tum["department_count"]) == (1, 1)
    assert [item["name"] for item in german.json()["institutions"]] == [
        "Technical University of Munich"
    ]


def test_departments_endpoint_is_scoped_and_unknown_institution_is_404(
    client: TestClient, settings: Settings
) -> None:
    register(client)
    seed(settings)
    institutions = client.get("/api/research/institutions").json()["institutions"]
    tum = next(item for item in institutions if "Munich" in item["name"])

    response = client.get(f"/api/research/institutions/{tum['id']}/departments")
    missing = client.get("/api/research/institutions/4242/departments")

    assert response.status_code == 200
    body = response.json()
    assert body["institution"]["id"] == tum["id"]
    assert [department["name"] for department in body["departments"]] == ["Physics"]
    assert body["departments"][0]["program_count"] == 1
    assert body["total"] == 1
    assert missing.status_code == 404
    assert error_code(missing) == "institution_missing"


def test_a_known_institution_without_departments_is_an_empty_page(
    client: TestClient, settings: Settings
) -> None:
    register(client)
    with db.session(settings.database_path) as connection:
        discovery.search_programs(
            connection,
            discovery.ProgramQuery(country="Germany"),
            adapter=FakeAdapter(records=(make_program(department=None),)),
            now=NOW,
        )
    institution = client.get("/api/research/institutions").json()["institutions"][0]

    response = client.get(f"/api/research/institutions/{institution['id']}/departments")

    assert response.status_code == 200
    assert response.json()["departments"] == []
    assert response.json()["total"] == 0
