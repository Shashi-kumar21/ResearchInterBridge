"""HTTP contract for matching and the shortlist (Phase 6c, ADR-022).

Service rules live in ``tests/test_matching.py`` and ``tests/test_shortlist.py``.
These tests check the HTTP layer: a session and a profile are required, ranked
matches explain themselves and put ineligible programs last, the shortlist is
per-user, excluded targets disappear from matches, and errors map to stable
codes. No test opens a socket.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from httpx import Response

from research_inter_bridge import db, discovery, recommendations
from research_inter_bridge import faculty as fac
from research_inter_bridge.api import create_app
from research_inter_bridge.config import Settings

PASSWORD = "correct horse battery staple"
CONSENTS = ["personal_data", "document_processing"]
ORIGIN = "http://localhost:3000"


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
class FacultyAdapter:
    records: tuple[fac.DiscoveredFaculty, ...]
    name: str = "fixture"
    parser_version: str = "1"
    contact_authority: str = "official"

    def search(self, query: fac.FacultyQuery) -> tuple[fac.DiscoveredFaculty, ...]:
        return self.records


@dataclass
class ProgramAdapter:
    records: tuple[discovery.DiscoveredProgram, ...]
    name: str = "fixture"
    parser_version: str = "1"

    def search(self, query: discovery.ProgramQuery) -> tuple[discovery.DiscoveredProgram, ...]:
        return self.records


def person(name: str, slug: str, interests: tuple[str, ...]) -> fac.DiscoveredFaculty:
    return fac.DiscoveredFaculty(
        full_name=name,
        institution="Example University",
        country="Germany",
        department="Computer Science",
        profile_url=f"https://www.cs.example.edu/people/{slug}",
        research_interests=interests,
    )


def program(
    name: str, slug: str, level: str, areas: tuple[str, ...]
) -> discovery.DiscoveredProgram:
    return discovery.DiscoveredProgram(
        name=name,
        institution="Example University",
        country="Germany",
        degree_level=level,
        official_url=f"https://example.edu/{slug}",
        research_areas=areas,
        status="open",
    )


def seed(settings: Settings) -> None:
    now = datetime.now(UTC)
    with db.session(settings.database_path) as connection:
        db.apply_migrations(connection)
        fac.search_faculty(
            connection,
            fac.FacultyQuery(),
            adapter=FacultyAdapter(
                records=(
                    person("Jane Doe", "jane", ("machine learning", "proteins")),
                    person("Ravi Kumar", "ravi", ("compilers",)),
                    person("Ada Byron", "ada", ("machine learning",)),
                )
            ),
            now=now,
        )
        discovery.search_programs(
            connection,
            discovery.ProgramQuery(),
            adapter=ProgramAdapter(
                records=(
                    program("PhD Machine Learning", "phd-ml", "PhD", ("machine learning",)),
                    program("MSc Internship", "msc", "MSc", ("compilers",)),
                )
            ),
            now=now,
        )


def sign_in(client: TestClient, email: str = "asha@example.edu", *, profile: bool = True) -> None:
    assert (
        client.post(
            "/api/auth/register", json={"email": email, "password": PASSWORD, "consents": CONSENTS}
        ).status_code
        == 201
    )
    if profile:
        response = client.put(
            "/api/profile",
            json={
                "full_name": "Asha Rao",
                "contact_email": email,
                "degree_type": "msc",
                "research_keywords": ["machine learning", "proteins"],
                "target_countries": ["Germany"],
            },
        )
        assert response.status_code == 200, response.text


def error_code(response: Response) -> str:
    return str(response.json()["error"]["code"])


def ids(response: Response, kind: str) -> dict[str, int]:
    return {
        item[kind]["full_name" if kind == "faculty" else "name"]: item[kind]["id"]
        for item in response.json()["items"]
    }


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("get", "/api/match/faculty"),
        ("get", "/api/match/programs"),
        ("get", "/api/shortlist"),
        ("post", "/api/shortlist"),
        ("post", "/api/shortlist/exclude"),
        ("post", "/api/shortlist/refresh"),
        ("delete", "/api/shortlist/faculty/1"),
    ],
)
def test_matching_routes_require_a_session(client: TestClient, method: str, path: str) -> None:
    kwargs = {"json": {"items": [{"type": "faculty", "id": 1}]}} if method == "post" else {}
    response = getattr(client, method)(path, **kwargs)

    assert response.status_code == 401
    assert error_code(response) == "invalid_session"


def test_matches_need_a_profile(client: TestClient, settings: Settings) -> None:
    seed(settings)
    sign_in(client, profile=False)

    response = client.get("/api/match/faculty")

    assert response.status_code == 404
    assert error_code(response) == "profile_missing"


def test_faculty_matches_are_ranked_and_explained(client: TestClient, settings: Settings) -> None:
    seed(settings)
    sign_in(client)

    response = client.get("/api/match/faculty")

    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert (body["total"], body["truncated"]) == (3, False)
    names = [item["faculty"]["full_name"] for item in body["items"]]
    assert names[0] == "Jane Doe"
    assert names[-1] == "Ravi Kumar"
    top = body["items"][0]
    assert top["match"]["eligibility"] == "not_assessed"
    research = next(f for f in top["match"]["factors"] if f["key"] == "research_interest")
    assert research["status"] == "met"
    assert set(research["matched"]) == {"machine learning", "protein"}
    assert top["shortlist_status"] is None
    assert "inputs_fingerprint" not in response.text


def test_an_ineligible_program_ranks_last_with_its_failed_gate(
    client: TestClient, settings: Settings
) -> None:
    seed(settings)
    sign_in(client)

    items = client.get("/api/match/programs").json()["items"]

    assert [item["program"]["name"] for item in items] == ["MSc Internship", "PhD Machine Learning"]
    phd = items[1]
    assert phd["match"]["eligibility"] == "ineligible"
    assert phd["match"]["score"] > items[0]["match"]["score"]
    degree = next(g for g in phd["match"]["gates"] if g["key"] == "degree")
    assert degree["status"] == "fail"


def test_shortlist_add_list_refresh_and_remove(client: TestClient, settings: Settings) -> None:
    seed(settings)
    sign_in(client)
    faculty = ids(client.get("/api/match/faculty"), "faculty")
    jane = {"type": "faculty", "id": faculty["Jane Doe"]}

    added = client.post("/api/shortlist", json={"items": [jane, jane]})
    again = client.post("/api/shortlist", json={"items": [jane]})
    listed = client.get("/api/shortlist").json()["items"]
    matches = client.get("/api/match/faculty").json()["items"]
    refreshed = client.post("/api/shortlist/refresh")
    removed = client.delete(f"/api/shortlist/faculty/{faculty['Jane Doe']}")
    missing = client.delete(f"/api/shortlist/faculty/{faculty['Jane Doe']}")

    assert added.status_code == 200
    assert added.json() == {"changed": [jane], "unchanged": []}
    assert again.json() == {"changed": [], "unchanged": [jane]}
    (entry,) = listed
    assert entry["target_name"] == "Jane Doe"
    assert entry["status"] == "shortlisted"
    assert entry["current"] is True
    assert entry["snapshot"]["score"] == matches[0]["match"]["score"]
    assert entry["snapshot"]["factors"][0]["key"] == "research_interest"
    assert matches[0]["shortlist_status"] == "shortlisted"
    assert refreshed.json() == {"changed": [], "unchanged": [jane]}
    assert removed.status_code == 204
    assert missing.status_code == 404
    assert error_code(missing) == "shortlist_item_missing"


def test_excluded_targets_leave_the_matches(client: TestClient, settings: Settings) -> None:
    seed(settings)
    sign_in(client)
    faculty = ids(client.get("/api/match/faculty"), "faculty")

    client.post(
        "/api/shortlist/exclude", json={"items": [{"type": "faculty", "id": faculty["Ravi Kumar"]}]}
    )
    hidden = client.get("/api/match/faculty").json()
    shown = client.get("/api/match/faculty", params={"include_excluded": "true"}).json()
    excluded = client.get("/api/shortlist", params={"status": "excluded"}).json()["items"]

    assert hidden["total"] == 2
    assert "Ravi Kumar" not in [item["faculty"]["full_name"] for item in hidden["items"]]
    assert shown["total"] == 3
    ravi = next(item for item in shown["items"] if item["faculty"]["full_name"] == "Ravi Kumar")
    assert ravi["shortlist_status"] == "excluded"
    assert [entry["target_name"] for entry in excluded] == ["Ravi Kumar"]


def test_an_unknown_target_adds_nothing(client: TestClient, settings: Settings) -> None:
    seed(settings)
    sign_in(client)
    faculty = ids(client.get("/api/match/faculty"), "faculty")

    response = client.post(
        "/api/shortlist",
        json={
            "items": [
                {"type": "faculty", "id": faculty["Jane Doe"]},
                {"type": "program", "id": 9999},
            ]
        },
    )

    assert response.status_code == 404
    assert error_code(response) == "program_missing"
    assert client.get("/api/shortlist").json()["items"] == []


@pytest.mark.parametrize(
    "body",
    [
        {"items": []},
        {"items": [{"type": "lab", "id": 1}]},
        {"items": [{"type": "faculty", "id": 0}]},
        {"items": [{"type": "faculty", "id": 1, "extra": True}]},
        {"items": [{"type": "faculty", "id": i} for i in range(1, 52)]},
    ],
)
def test_invalid_shortlist_bodies_are_validation_errors(client: TestClient, body: dict) -> None:
    sign_in(client)

    response = client.post("/api/shortlist", json=body)

    assert response.status_code == 422
    assert error_code(response) == "invalid_request"


def test_a_full_shortlist_is_a_conflict(
    client: TestClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    from research_inter_bridge import shortlist

    seed(settings)
    sign_in(client)
    faculty = ids(client.get("/api/match/faculty"), "faculty")
    monkeypatch.setattr(shortlist, "MAX_ITEMS", 1)
    client.post("/api/shortlist", json={"items": [{"type": "faculty", "id": faculty["Jane Doe"]}]})

    response = client.post(
        "/api/shortlist", json={"items": [{"type": "faculty", "id": faculty["Ada Byron"]}]}
    )

    assert response.status_code == 409
    assert error_code(response) == "shortlist_full"


def test_one_students_shortlist_is_invisible_to_another(
    settings: Settings, client: TestClient
) -> None:
    seed(settings)
    sign_in(client)
    faculty = ids(client.get("/api/match/faculty"), "faculty")
    client.post("/api/shortlist", json={"items": [{"type": "faculty", "id": faculty["Jane Doe"]}]})

    with TestClient(create_app(settings)) as other:
        sign_in(other, email="ben@example.edu")
        assert other.get("/api/shortlist").json()["items"] == []
        matches = other.get("/api/match/faculty").json()["items"]
        assert all(item["shortlist_status"] is None for item in matches)
        gone = other.delete(f"/api/shortlist/faculty/{faculty['Jane Doe']}")
        assert gone.status_code == 404

    assert len(client.get("/api/shortlist").json()["items"]) == 1


def test_matches_page_and_report_truncation(
    client: TestClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed(settings)
    sign_in(client)
    monkeypatch.setattr(recommendations, "MAX_CANDIDATES", 2)

    first = client.get("/api/match/faculty", params={"limit": 1}).json()
    second = client.get("/api/match/faculty", params={"limit": 1, "offset": 1}).json()

    assert (first["total"], first["truncated"]) == (2, True)
    assert first["items"][0]["faculty"]["id"] != second["items"][0]["faculty"]["id"]


def test_scoring_routes_share_a_per_account_rate_limit(
    settings: Settings, client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    from research_inter_bridge import ratelimit

    seed(settings)
    sign_in(client)
    monkeypatch.setattr(
        ratelimit,
        "MATCH_RULE",
        ratelimit.RateLimitRule(name="match.score", limit=3, window_seconds=300),
    )

    listings = [client.get("/api/shortlist") for _ in range(5)]
    allowed = [
        client.get("/api/match/faculty"),
        client.get("/api/match/programs"),
        client.post("/api/shortlist/refresh"),
    ]
    refused = client.get("/api/match/faculty")
    listing = client.get("/api/shortlist")

    assert [response.status_code for response in allowed] == [200, 200, 200]
    assert refused.status_code == 429
    assert error_code(refused) == "rate_limited"
    assert refused.headers["retry-after"].isdigit()
    # Listing re-scores nothing (QA n5 on #39), so it never spends the budget:
    # five reads before and one after the limit all succeed.
    assert {response.status_code for response in listings} == {200}
    assert listing.status_code == 200

    # The budget is per account: another student on the same address is unaffected.
    with TestClient(create_app(settings)) as other:
        sign_in(other, email="ben@example.edu")
        assert other.get("/api/match/faculty").status_code == 200


def test_a_matching_error_has_a_stable_code_and_no_internals(
    client: TestClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    from research_inter_bridge.matching import MatchingError

    seed(settings)
    sign_in(client)

    def broken(*args: object, **kwargs: object) -> None:
        raise MatchingError("faculty 7 was loaded as a browse summary; internal detail")

    monkeypatch.setattr(recommendations, "faculty_matches", broken)

    response = client.get("/api/match/faculty")

    assert response.status_code == 500
    assert error_code(response) == "invalid_matching"
    assert "internal detail" not in response.text


def test_paging_reuses_the_ranked_list_instead_of_rescoring(
    client: TestClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Owner priority (low latency, low cost): "Load more" slices a cached list.
    from research_inter_bridge import matching

    recommendations.clear_cache()
    seed(settings)
    sign_in(client)
    real = matching.score_faculty
    scored: list[int] = []

    def counting(*args: object, **kwargs: object):
        scored.append(1)
        return real(*args, **kwargs)  # type: ignore[arg-type]

    monkeypatch.setattr(matching, "score_faculty", counting)

    first = client.get("/api/match/faculty", params={"limit": 1})
    after_first = len(scored)
    second = client.get("/api/match/faculty", params={"limit": 1, "offset": 1})

    assert after_first == 3
    assert len(scored) == after_first
    assert first.json()["items"][0]["faculty"]["id"] != second.json()["items"][0]["faculty"]["id"]

    # A profile change is a different key, so the list is scored again.
    client.put(
        "/api/profile",
        json={
            "full_name": "Asha Rao",
            "contact_email": "asha@example.edu",
            "degree_type": "msc",
            "research_keywords": ["compilers"],
            "target_countries": ["Germany"],
        },
    )
    reranked = client.get("/api/match/faculty").json()["items"]
    assert len(scored) == after_first + 3
    assert reranked[0]["faculty"]["full_name"] == "Ravi Kumar"


def test_a_shortlist_change_is_visible_on_the_next_match_read(
    client: TestClient, settings: Settings
) -> None:
    recommendations.clear_cache()
    seed(settings)
    sign_in(client)
    first = client.get("/api/match/faculty").json()["items"]
    jane = next(item for item in first if item["faculty"]["full_name"] == "Jane Doe")

    client.post(
        "/api/shortlist", json={"items": [{"type": "faculty", "id": jane["faculty"]["id"]}]}
    )
    again = client.get("/api/match/faculty").json()["items"]

    assert (
        next(i for i in again if i["faculty"]["id"] == jane["faculty"]["id"])["shortlist_status"]
        == "shortlisted"
    )


def test_the_ranked_cache_keeps_no_records_and_pages_load_summaries(
    client: TestClient, settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    # QA B1 on #41 (hosting cost): caching full faculty records with papers and
    # abstracts cost ~32 MB per list. Only ids, results and statuses are kept,
    # and a page loads summaries (highlights and a count) for its rows only.
    recommendations.clear_cache()
    seed(settings)
    sign_in(client)
    loads: list[tuple[int, bool]] = []
    real = fac._load_faculty

    def spy(connection, ids, *, now, include_publications=True):  # type: ignore[no-untyped-def]
        loads.append((len(ids), include_publications))
        return real(connection, ids, now=now, include_publications=include_publications)

    monkeypatch.setattr(fac, "_load_faculty", spy)

    response = client.get("/api/match/faculty", params={"limit": 2})

    assert response.status_code == 200
    (entry,) = recommendations._cache.values()
    ranked = entry[1]
    assert len(ranked) == 3
    for item in ranked:
        assert not hasattr(item, "record")
        assert set(item.__slots__) == {"target_id", "result", "shortlist_status"}
    # The page itself is loaded as a 2-row summary, not as full records.
    assert (2, False) in loads
    assert all(row["faculty"]["publication_count"] == 0 for row in response.json()["items"])
