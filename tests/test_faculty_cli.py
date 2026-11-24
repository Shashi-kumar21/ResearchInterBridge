from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest

from research_inter_bridge import cli as cli_module
from research_inter_bridge import db
from research_inter_bridge import faculty as fac
from research_inter_bridge.config import Settings
from research_inter_bridge.errors import DiscoveryError, DiscoveryUnavailableError
from research_inter_bridge.faculty_pages import OfficialPageFacultyAdapter
from research_inter_bridge.openalex import OrcidPublicationsAdapter

PROFILE = "https://www.cs.example.edu/people/jane.doe"
OTHER = "https://www.cs.example.edu/people/ravi"


def person(**overrides: object) -> fac.DiscoveredFaculty:
    base: dict[str, object] = {
        "full_name": "Jane Doe",
        "institution": "Example University",
        "country": "Germany",
        "department": "Computer Science",
        "profile_url": PROFILE,
        "email": "jane.doe@cs.example.edu",
        "email_source_url": PROFILE,
        "research_interests": ("machine learning",),
        "publications": (
            fac.DiscoveredPublication(title="Graph Learning", doi="10.1234/g", year=2025),
        ),
    }
    base.update(overrides)
    return fac.DiscoveredFaculty(**base)  # type: ignore[arg-type]


@dataclass
class FakeAdapter:
    records: tuple[fac.DiscoveredFaculty, ...]
    name: str = "fixture"
    parser_version: str = "1"
    contact_authority: str = "official"

    def search(self, query: fac.FacultyQuery) -> tuple[fac.DiscoveredFaculty, ...]:
        return self.records


def seed(settings: Settings, *records: fac.DiscoveredFaculty) -> None:
    with db.session(settings.database_path) as connection:
        db.apply_migrations(connection)
        fac.search_faculty(
            connection,
            fac.FacultyQuery(country="Germany"),
            adapter=FakeAdapter(records=records),
            now=datetime.now(UTC),
        )


def test_offline_lists_cached_faculty_with_papers(cli) -> None:
    seed(cli.settings, person())

    code, out = cli.run("faculty", "--offline", "--area", "machine")

    assert code == 0
    payload = json.loads(out)
    assert payload["served_from_cache"] is True
    assert payload["stale"] is False
    (member,) = payload["faculty"]
    assert member["full_name"] == "Jane Doe"
    assert member["email"] == "jane.doe@cs.example.edu"
    assert member["email_status"] == "verified"
    assert member["contact_sendable"] is True
    assert member["highlighted_publications"][0]["title"] == "Graph Learning"
    assert member["publication_count"] == 1


def test_an_unverified_address_is_never_printed(cli) -> None:
    seed(cli.settings, person(email_source_url="https://janedoe.github.io/"))

    code, out = cli.run("faculty", "--offline")

    assert code == 0
    (member,) = json.loads(out)["faculty"]
    assert member["email"] is None
    assert member["email_status"] == "unverified"
    assert member["email_source_url"] == "https://janedoe.github.io/"
    assert member["contact_sendable"] is False
    assert "jane.doe@cs.example.edu" not in out


def test_offline_filters_by_institution_and_department(cli) -> None:
    seed(
        cli.settings,
        person(),
        person(
            full_name="Ravi Kumar",
            profile_url=OTHER,
            email_source_url=OTHER,
            department="Biology",
        ),
    )

    code, out = cli.run("faculty", "--offline", "--department", "bio")

    assert code == 0
    assert [m["full_name"] for m in json.loads(out)["faculty"]] == ["Ravi Kumar"]


def test_offline_on_an_empty_cache_prints_a_message(cli) -> None:
    code, out = cli.run("faculty", "--offline")

    assert code == 0
    payload = json.loads(out)
    assert payload["faculty"] == []
    assert "No cached faculty" in payload["message"]


def test_without_a_source_or_cache_the_blocker_is_explained(cli) -> None:
    code, out = cli.run("faculty", "--country", "Germany")

    assert code == 1
    assert out == ""
    assert "no faculty discovery adapter" in cli.last_err


def test_offline_and_a_live_source_are_mutually_exclusive(cli) -> None:
    code, out = cli.run("faculty", "--offline", "--source-url", "https://example.edu/people")

    assert code == 1
    assert out == ""
    assert "--offline cannot be combined with --source-url" in cli.last_err


def test_live_discovery_names_the_missing_contact_setting(
    cli, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv("RIB_RESEARCH_CONTACT_EMAIL", raising=False)

    code, out = cli.run(
        "faculty",
        "--source-url",
        "https://example.edu/people",
        "--source-institution",
        "Example University",
    )

    assert code == 1
    assert out == ""
    assert "RIB_RESEARCH_CONTACT_EMAIL" in cli.last_err


@pytest.mark.parametrize(("flags", "with_papers"), [((), True), (("--no-papers",), False)])
def test_live_discovery_passes_its_configuration_to_the_builder(
    cli, monkeypatch: pytest.MonkeyPatch, flags: tuple[str, ...], with_papers: bool
) -> None:
    monkeypatch.setenv("RIB_RESEARCH_CONTACT_EMAIL", "research@example.org")
    captured: dict[str, object] = {}

    def fake_builder(settings, source_urls, **options):
        captured["contact"] = settings.research_contact_email
        captured["urls"] = list(source_urls)
        captured.update(options)
        return FakeAdapter(records=(person(),))

    monkeypatch.setattr(cli_module, "_build_faculty_adapter", fake_builder)

    code, out = cli.run(
        "faculty",
        "--source-url",
        "https://www.cs.example.edu/people",
        "--source-url",
        "https://www.cs.example.edu/people/jane.doe",
        "--source-institution",
        "Example University",
        "--source-country",
        "Germany",
        "--source-department",
        "Computer Science",
        *flags,
    )

    assert code == 0
    assert json.loads(out)["served_from_cache"] is False
    assert captured == {
        "contact": "research@example.org",
        "urls": [
            "https://www.cs.example.edu/people",
            "https://www.cs.example.edu/people/jane.doe",
        ],
        "institution": "Example University",
        "country": "Germany",
        "department": "Computer Science",
        "with_papers": with_papers,
    }


def test_the_builder_wraps_official_pages_with_orcid_papers(tmp_path: Path) -> None:
    settings = Settings(
        database_path=tmp_path / "app.db",
        data_directory=tmp_path / "data",
        research_contact_email="research@example.org",
    )

    with_papers = cli_module._build_faculty_adapter(
        settings,
        ["https://www.cs.example.edu/people"],
        institution=" Example University ",
        country="Germany",
        department=None,
        with_papers=True,
    )
    without = cli_module._build_faculty_adapter(
        settings,
        ["https://www.cs.example.edu/people"],
        institution="Example University",
        country=None,
        department=None,
        with_papers=False,
    )

    assert isinstance(with_papers, OrcidPublicationsAdapter)
    assert with_papers.contact_authority == "official"
    assert isinstance(without, OfficialPageFacultyAdapter)
    assert without.sources[0].institution == "Example University"


def test_the_builder_refuses_without_a_contact_or_a_source(tmp_path: Path) -> None:
    settings = Settings(database_path=tmp_path / "app.db", data_directory=tmp_path / "data")

    with pytest.raises(DiscoveryUnavailableError, match="RIB_RESEARCH_CONTACT_EMAIL"):
        cli_module._build_faculty_adapter(
            settings,
            ["https://www.cs.example.edu/people"],
            institution="Example University",
            country=None,
            department=None,
            with_papers=True,
        )
    with pytest.raises(DiscoveryError, match="at least one --source-url"):
        cli_module._build_faculty_adapter(
            settings, [], institution=None, country=None, department=None, with_papers=True
        )


@pytest.mark.parametrize("institution", [None, "", "   "])
def test_live_faculty_discovery_requires_the_source_institution(
    tmp_path: Path, institution: str | None
) -> None:
    settings = Settings(
        database_path=tmp_path / "app.db",
        data_directory=tmp_path / "data",
        research_contact_email="research@example.org",
    )

    with pytest.raises(DiscoveryError, match="--source-institution is required"):
        cli_module._build_faculty_adapter(
            settings,
            ["https://www.cs.example.edu/people"],
            institution=institution,
            country=None,
            department=None,
            with_papers=True,
        )


def test_the_cli_reports_a_missing_source_institution(cli, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("RIB_RESEARCH_CONTACT_EMAIL", "research@example.org")

    code, out = cli.run("faculty", "--source-url", "https://www.cs.example.edu/people")

    assert code == 1
    assert out == ""
    assert "--source-institution is required" in cli.last_err


def test_a_stale_verified_address_is_shown_but_not_sendable(cli) -> None:
    # Owner decision (ADR-021): the address stays visible with `stale` and
    # `contact_sendable: false`; outreach gates on contact_sendable only.
    with db.session(cli.settings.database_path) as connection:
        db.apply_migrations(connection)
        fac.search_faculty(
            connection,
            fac.FacultyQuery(),
            adapter=FakeAdapter(records=(person(),)),
            now=datetime(2026, 1, 1, tzinfo=UTC),
        )

    code, out = cli.run("faculty", "--offline")

    assert code == 0
    (member,) = json.loads(out)["faculty"]
    assert member["stale"] is True
    assert member["email"] == "jane.doe@cs.example.edu"
    assert member["email_status"] == "verified"
    assert member["contact_sendable"] is False


def test_public_email_shows_only_verified_addresses(tmp_path: Path) -> None:
    with db.session(tmp_path / "app.db") as connection:
        db.apply_migrations(connection)
        result = fac.search_faculty(
            connection,
            fac.FacultyQuery(),
            adapter=FakeAdapter(
                records=(
                    person(),
                    person(
                        full_name="Ravi Kumar",
                        profile_url=OTHER,
                        email="ravi@cs.example.edu",
                        email_source_url="https://ravi.example.org/",
                    ),
                    person(
                        full_name="Ada Byron",
                        profile_url="https://www.cs.example.edu/people/ada",
                        email=None,
                        email_source_url=None,
                    ),
                )
            ),
        )

    shown = {member.full_name: fac.public_email(member) for member in result.faculty}
    assert shown == {
        "Ada Byron": None,
        "Jane Doe": "jane.doe@cs.example.edu",
        "Ravi Kumar": None,
    }
