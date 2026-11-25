"""Cross-user result-set reuse (task.md §13.4 items 3-5, acceptance test 33, ADR-023).

Checks: buckets use derived, non-identifying features only; reuse needs at
least three distinct students; only candidate ids are shared and every student
is re-scored privately; each reuse is audited without personal content; a
reuse makes no outbound call; ``other`` never reuses.
"""

from __future__ import annotations

import json
import socket
import time
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from research_inter_bridge import db, discovery, profiles, recommendations, result_reuse, taxonomy
from research_inter_bridge import faculty as fac
from research_inter_bridge.api import create_app
from research_inter_bridge.config import Settings
from research_inter_bridge.profiles import DegreeType, StudentProfile

PASSWORD = "correct horse battery staple"
CONSENTS = ["personal_data", "document_processing"]


@pytest.fixture
def settings(tmp_path: Path) -> Settings:
    return Settings(database_path=tmp_path / "api.db", data_directory=tmp_path / "data")


@pytest.fixture(autouse=True)
def _fresh_cache() -> Iterator[None]:
    recommendations.clear_cache()
    yield
    recommendations.clear_cache()


@dataclass
class FacultyAdapter:
    records: tuple[fac.DiscoveredFaculty, ...]
    name: str = "fixture"
    parser_version: str = "1"
    contact_authority: str = "official"

    def search(self, query: fac.FacultyQuery) -> tuple[fac.DiscoveredFaculty, ...]:
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


def seed(settings: Settings) -> None:
    with db.session(settings.database_path) as connection:
        db.apply_migrations(connection)
        fac.search_faculty(
            connection,
            fac.FacultyQuery(),
            adapter=FacultyAdapter(
                records=(
                    person("Jane Doe", "jane", ("machine learning", "proteins")),
                    person("Ada Byron", "ada", ("deep learning",)),
                    person("Ravi Kumar", "ravi", ("medieval history",)),
                )
            ),
            now=datetime.now(UTC),
        )


def student(
    settings: Settings, email: str, keywords: list[str], *, name: str = "Student"
) -> TestClient:
    client = TestClient(create_app(settings))
    client.__enter__()
    assert (
        client.post(
            "/api/auth/register",
            json={"email": email, "password": PASSWORD, "consents": CONSENTS},
        ).status_code
        == 201
    )
    response = client.put(
        "/api/profile",
        json={
            "full_name": name,
            "contact_email": email,
            "degree_type": "msc",
            "research_keywords": keywords,
            "target_countries": ["Germany"],
        },
    )
    assert response.status_code == 200, response.text
    return client


def audit_rows(settings: Settings) -> list[dict]:
    with db.session(settings.database_path) as connection:
        return [
            {
                "actor": row["actor"],
                "target_id": row["target_id"],
                **json.loads(row["metadata_json"]),
            }
            for row in connection.execute(
                "SELECT actor, target_id, metadata_json FROM audit_events "
                "WHERE action = 'match.result_reuse' ORDER BY id"
            )
        ]


def stored_sets(settings: Settings) -> list[dict]:
    with db.session(settings.database_path) as connection:
        return [dict(row) for row in connection.execute("SELECT * FROM bucket_result_sets")]


def base_profile(**overrides: object) -> StudentProfile:
    values: dict[str, object] = {
        "user_id": 1,
        "full_name": "Asha Rao",
        "contact_email": "asha@example.edu",
        "degree_type": DegreeType.MSC,
        "research_keywords": ("machine learning",),
        "target_countries": ("Germany",),
    }
    values.update(overrides)
    return StudentProfile(**values)  # type: ignore[arg-type]


# --------------------------------------------------------------------------- #
# buckets
# --------------------------------------------------------------------------- #


def test_the_bucket_uses_only_derived_non_identifying_features() -> None:
    first = result_reuse.bucket_for(base_profile())
    other_person = result_reuse.bucket_for(
        base_profile(
            user_id=2,
            full_name="Ben Ode",
            contact_email="ben@example.edu",
            gpa=3.9,
            gpa_scale=4.0,
            skills=("Rust",),
            research_keywords=("deep learning",),  # same OECD field as machine learning
        )
    )
    other_degree = result_reuse.bucket_for(base_profile(degree_type=DegreeType.PHD))
    other_country = result_reuse.bucket_for(base_profile(target_countries=("Japan",)))

    assert first is not None and other_person is not None
    assert first.id == other_person.id
    assert first.fields == ("1.2",)
    assert other_degree is not None and other_degree.id != first.id
    assert other_country is not None and other_country.id != first.id
    for private in ("Asha", "asha@example.edu", "machine learning"):
        assert private not in repr(first)


def test_other_only_keywords_have_no_bucket() -> None:
    assert taxonomy.fields_for(["basket weaving"]) == {taxonomy.OTHER}
    assert result_reuse.bucket_for(base_profile(research_keywords=("basket weaving",))) is None
    assert result_reuse.bucket_for(base_profile(research_keywords=())) is None


def test_every_keyword_field_is_an_oecd_field() -> None:
    assert len(taxonomy.FIELDS) == 42
    assert set(taxonomy.KEYWORDS) <= set(taxonomy.FIELDS)


@pytest.mark.parametrize(
    ("keywords", "fields"),
    [
        (["protein design"], {"1.6"}),
        (["programming languages"], {"1.2"}),
        (["molecular dynamics"], {"other"}),
        (["software engineering"], {"1.2"}),
        (["graph neural networks"], {"1.2"}),
    ],
)
def test_generic_words_do_not_pull_in_unrelated_fields(
    keywords: list[str], fields: set[str]
) -> None:
    assert taxonomy.fields_for(keywords) == fields


# --------------------------------------------------------------------------- #
# k >= 3 and reuse
# --------------------------------------------------------------------------- #


def test_two_students_never_reuse(settings: Settings) -> None:
    seed(settings)
    a = student(settings, "a@example.edu", ["machine learning"])
    b = student(settings, "b@example.edu", ["deep learning"])

    a.get("/api/match/faculty")
    b.get("/api/match/faculty")

    assert stored_sets(settings) == []
    assert audit_rows(settings) == []


def test_the_third_student_builds_and_later_students_reuse(settings: Settings) -> None:
    seed(settings)
    a = student(settings, "a@example.edu", ["machine learning"])
    b = student(settings, "b@example.edu", ["deep learning"])
    c = student(settings, "c@example.edu", ["neural networks"])
    for client in (a, b):
        client.get("/api/match/faculty")

    c_items = c.get("/api/match/faculty").json()["items"]
    (built,) = stored_sets(settings)
    assert audit_rows(settings) == []
    # The set holds candidate ids of public records only: the history-only
    # professor shares no OECD field with the bucket and is not a candidate.
    assert set(json.loads(built["candidate_ids_json"])) == {
        item["faculty"]["id"] for item in c_items
    }
    assert "Ravi Kumar" not in [item["faculty"]["full_name"] for item in c_items]
    assert set(built) == {
        "bucket_id",
        "target_type",
        "query_fingerprint",
        "candidate_ids_json",
        "truncated",
        "created_at",
        "expires_at",
    }

    recommendations.clear_cache()
    a.get("/api/match/faculty")

    (reuse,) = audit_rows(settings)
    # Audit metadata values are stringified by db.redact_mapping.
    assert reuse["reused_ids"] == [str(i) for i in json.loads(built["candidate_ids_json"])]
    assert reuse["bucket_members"] == 3


def test_acceptance_33_reuse_makes_no_outbound_call_and_leaks_nothing(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    seed(settings)
    first = student(
        settings, "first@example.edu", ["machine learning", "proteins"], name="First Person"
    )
    second = student(
        settings, "second@example.edu", ["deep learning", "protein design"], name="Second Person"
    )
    third = student(
        settings, "third@example.edu", ["neural networks", "genomics"], name="Third Person"
    )
    for client in (first, second, third):
        client.get("/api/match/faculty")
    recommendations.clear_cache()

    def no_network(*args: object, **kwargs: object) -> None:
        raise AssertionError("a reuse must not make an outbound call")

    monkeypatch.setattr(socket.socket, "connect", no_network)
    monkeypatch.setattr(fac, "search_faculty", no_network)
    monkeypatch.setattr(discovery, "search_programs", no_network)

    response = second.get("/api/match/faculty")

    assert response.status_code == 200
    assert len(audit_rows(settings)) == 1
    for private in ("first@example.edu", "First Person", "Third Person", "genomics"):
        assert private not in response.text
    (row,) = audit_rows(settings)
    assert row["actor"] == "system"
    assert set(row) == {
        "actor",
        "target_id",
        "query_fingerprint",
        "target_type",
        "reused_ids",
        "bucket_members",
    }
    serialised = json.dumps(row)
    for private in ("second@example.edu", "Second Person", "deep learning", "user:"):
        assert private not in serialised


def test_shared_candidates_are_rescored_per_student(settings: Settings) -> None:
    seed(settings)
    # Same OECD fields (1.2 and 1.6), different keywords, so different scores.
    a = student(settings, "a@example.edu", ["machine learning", "proteins"])
    b = student(settings, "b@example.edu", ["deep learning", "genomics"])
    c = student(settings, "c@example.edu", ["neural networks", "protein folding"])
    for client in (a, b, c):
        client.get("/api/match/faculty")
    recommendations.clear_cache()

    a_scores = {
        i["faculty"]["full_name"]: i["match"]["score"]
        for i in a.get("/api/match/faculty").json()["items"]
    }
    b_scores = {
        i["faculty"]["full_name"]: i["match"]["score"]
        for i in b.get("/api/match/faculty").json()["items"]
    }

    assert set(a_scores) == set(b_scores)
    assert a_scores != b_scores
    assert len(audit_rows(settings)) == 2


def test_other_students_never_reuse(settings: Settings) -> None:
    seed(settings)
    clients = [student(settings, f"o{index}@example.edu", ["basket weaving"]) for index in range(3)]
    for client in clients:
        client.get("/api/match/faculty")
    recommendations.clear_cache()
    clients[0].get("/api/match/faculty")

    assert stored_sets(settings) == []
    assert audit_rows(settings) == []
    with db.session(settings.database_path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM profile_buckets").fetchone()[0] == 0


def test_an_expired_set_is_rebuilt_not_reused(
    settings: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    from datetime import timedelta

    seed(settings)
    monkeypatch.setattr(result_reuse, "RESULT_SET_TTL", timedelta(seconds=-1))
    clients = [student(settings, f"e{i}@example.edu", ["machine learning"]) for i in range(3)]
    for client in clients:
        client.get("/api/match/faculty")
    recommendations.clear_cache()
    clients[0].get("/api/match/faculty")

    assert audit_rows(settings) == []


# --------------------------------------------------------------------------- #
# privacy plumbing
# --------------------------------------------------------------------------- #


def test_bucket_membership_is_deleted_with_the_account_and_exported(settings: Settings) -> None:
    seed(settings)
    client = student(settings, "gone@example.edu", ["machine learning"])
    client.get("/api/match/faculty")

    exported = client.get("/api/auth/export").json()["profile_bucket"]
    assert exported is not None and len(exported["bucket_id"]) == 16
    with db.session(settings.database_path) as connection:
        user_id = connection.execute("SELECT user_id FROM profile_buckets").fetchone()[0]
        profiles.delete_user(connection, user_id, actor="test")
        assert connection.execute("SELECT COUNT(*) FROM profile_buckets").fetchone()[0] == 0


def test_expired_ranked_lists_are_swept_on_put(monkeypatch: pytest.MonkeyPatch) -> None:
    # QA n2 on #41: an idle worker must not keep stale lists alive.
    recommendations._cache_put("old", (), False)
    monkeypatch.setattr(recommendations, "CACHE_TTL_SECONDS", -1)
    recommendations._cache_put("new", (), False)

    assert "old" not in recommendations._cache
    assert recommendations.CACHE_MAX_ENTRIES == 64
    assert time.monotonic() > 0


def test_reaching_k_keeps_unmapped_and_blank_records(settings: Settings) -> None:
    # QA B1 on #42: a student's list must not lose records the taxonomy cannot
    # place ("other") or that state no areas once the bucket reaches k = 3.
    seed(settings)
    with db.session(settings.database_path) as connection:
        # A different query: the same one would be served from the cache.
        fac.search_faculty(
            connection,
            fac.FacultyQuery(country="Germany"),
            adapter=FacultyAdapter(
                records=(
                    person("Mo Dyn", "mo", ("molecular dynamics",)),
                    person("Bea Blank", "bea", ()),
                )
            ),
            now=datetime.now(UTC),
        )
    first = student(settings, "k1@example.edu", ["machine learning", "molecular dynamics"])
    before = {i["faculty"]["full_name"] for i in first.get("/api/match/faculty").json()["items"]}
    for index in (2, 3):
        student(settings, f"k{index}@example.edu", ["machine learning"]).get("/api/match/faculty")
    recommendations.clear_cache()
    after = {i["faculty"]["full_name"] for i in first.get("/api/match/faculty").json()["items"]}

    assert {"Jane Doe", "Ada Byron", "Mo Dyn", "Bea Blank", "Ravi Kumar"} <= before
    # Only a record whose areas all map to known fields outside the bucket
    # (medieval history -> 6.1) is left out; nothing unknown is dropped.
    assert after == before - {"Ravi Kumar"}
    assert len(audit_rows(settings)) == 1


def test_an_unchanged_bucket_is_not_rewritten(settings: Settings) -> None:
    # QA n1 on #42: a read, not a write, on every match miss.
    seed(settings)
    client = student(settings, "same@example.edu", ["machine learning"])
    client.get("/api/match/faculty")
    with db.session(settings.database_path) as connection:
        first = connection.execute("SELECT updated_at FROM profile_buckets").fetchone()[0]
    recommendations.clear_cache()
    client.get("/api/match/faculty")
    with db.session(settings.database_path) as connection:
        again = connection.execute("SELECT updated_at FROM profile_buckets").fetchone()[0]

    assert again == first
