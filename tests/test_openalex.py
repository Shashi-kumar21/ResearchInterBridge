from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from research_inter_bridge import db
from research_inter_bridge import faculty as fac
from research_inter_bridge.errors import ConfigError, DiscoveryError
from research_inter_bridge.http_source import SourceResponse
from research_inter_bridge.openalex import (
    LIST_AUTHORSHIP_LIMIT,
    OPENALEX_WORKS_URL,
    WORKS_PER_PERSON,
    OrcidPublicationsAdapter,
    abstract_from_inverted_index,
    publications_from_works,
    works_url,
)

NOW = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
ORCID = "0000-0002-1825-0097"
OTHER_ORCID = "0000-0002-9079-593X"
CONTACT = "research-contact@example.org"
PROFILE = "https://www.cs.example.edu/people/jane.doe"


def author(name: str, orcid: str | None = None) -> dict[str, object]:
    return {
        "author": {
            "display_name": name,
            "orcid": None if orcid is None else f"https://orcid.org/{orcid}",
        }
    }


def work(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "id": "https://openalex.org/W100",
        "doi": "https://doi.org/10.1234/GRAPH.2025",
        "display_name": "Graph Learning for Proteins",
        "publication_year": 2025,
        "authorships": [author("Jane Doe", ORCID), author("Ravi Kumar")],
        "primary_location": {
            "landing_page_url": "https://journal.example.org/graph",
            "source": {"display_name": "Journal of Examples"},
        },
        "abstract_inverted_index": {"We": [0], "learn": [1], "graphs": [2]},
    }
    base.update(overrides)
    return base


def person(**overrides: object) -> fac.DiscoveredFaculty:
    base: dict[str, object] = {
        "full_name": "Jane Doe",
        "institution": "Example University",
        "country": "Germany",
        "department": "Computer Science",
        "profile_url": PROFILE,
        "email": "jane.doe@cs.example.edu",
        "email_source_url": PROFILE,
        "orcid": ORCID,
        "research_interests": ("machine learning",),
    }
    base.update(overrides)
    return fac.DiscoveredFaculty(**base)  # type: ignore[arg-type]


@dataclass
class Inner:
    records: tuple[fac.DiscoveredFaculty, ...]
    name: str = "official_faculty_page"
    parser_version: str = "1"
    contact_authority: str = "official"

    def search(self, query: fac.FacultyQuery) -> tuple[fac.DiscoveredFaculty, ...]:
        return self.records


@dataclass
class FakeClient:
    """Returns one OpenAlex payload per ORCID iD; records every URL; no socket."""

    payloads: dict[str, object]
    calls: list[str] = field(default_factory=list)
    accepts: list[str] = field(default_factory=list)

    def get(self, url: str, *, accept: str = "") -> SourceResponse:
        self.calls.append(url)
        self.accepts.append(accept)
        orcid = parse_qs(urlsplit(url).query)["filter"][0].removeprefix("author.orcid:")
        body = self.payloads[orcid]
        raw = body if isinstance(body, bytes) else json.dumps(body).encode("utf-8")
        return SourceResponse(
            status=200, url=url, content_type="application/json; charset=utf-8", body=raw
        )


def adapter_for(
    records: tuple[fac.DiscoveredFaculty, ...], payloads: dict[str, object]
) -> tuple[OrcidPublicationsAdapter, FakeClient]:
    client = FakeClient(payloads)
    adapter = OrcidPublicationsAdapter(
        Inner(records),
        client,  # type: ignore[arg-type]
        CONTACT,
        now=NOW,
    )
    return adapter, client


# --------------------------------------------------------------------------- #
# request shape
# --------------------------------------------------------------------------- #


def test_the_works_query_is_keyed_by_orcid_and_uses_the_polite_pool() -> None:
    url = works_url(ORCID, CONTACT)
    parts = urlsplit(url)
    query = parse_qs(parts.query)

    assert f"{parts.scheme}://{parts.netloc}{parts.path}" == OPENALEX_WORKS_URL
    assert query["filter"] == [f"author.orcid:{ORCID}"]
    assert query["sort"] == ["publication_year:desc"]
    assert query["per-page"] == [str(WORKS_PER_PERSON)]
    assert query["mailto"] == [CONTACT]
    assert "search" not in query  # never a name search


# --------------------------------------------------------------------------- #
# mapping
# --------------------------------------------------------------------------- #


def test_maps_only_stated_fields() -> None:
    (publication,) = publications_from_works({"results": [work()]}, ORCID, now=NOW)

    assert publication.title == "Graph Learning for Proteins"
    assert publication.doi == "10.1234/graph.2025"
    assert publication.url == "https://journal.example.org/graph"
    assert publication.year == 2025
    assert publication.authors == ("Jane Doe", "Ravi Kumar")
    assert publication.venue == "Journal of Examples"
    assert publication.abstract == "We learn graphs"


def test_a_work_without_the_orcid_in_its_authorships_is_dropped() -> None:
    # Same author *name*, different (or no) ORCID: the filter result is not
    # trusted on its own and names are never compared.
    payload = {
        "results": [
            work(authorships=[author("Jane Doe", OTHER_ORCID)]),
            work(authorships=[author("Jane Doe")]),
            work(authorships="not a list"),
            work(id="https://openalex.org/W2", display_name="Kept", doi=None),
        ]
    }

    publications = publications_from_works(payload, ORCID, now=NOW)

    assert [p.title for p in publications] == ["Kept"]


def test_unknown_fields_stay_unknown() -> None:
    (publication,) = publications_from_works(
        {
            "results": [
                work(
                    doi="not-a-doi",
                    publication_year=None,
                    primary_location=None,
                    abstract_inverted_index=None,
                )
            ]
        },
        ORCID,
        now=NOW,
    )

    assert publication.doi is None
    assert publication.url == "https://openalex.org/W100"
    assert publication.year is None
    assert publication.venue is None
    assert publication.abstract is None


@pytest.mark.parametrize("year", [2028, 1700, "2025", True])
def test_an_impossible_year_is_left_unknown(year: object) -> None:
    (publication,) = publications_from_works(
        {"results": [work(publication_year=year)]}, ORCID, now=NOW
    )

    assert publication.year is None


def test_an_author_list_with_a_missing_name_is_not_copied() -> None:
    authorships = [author("Jane Doe", ORCID), {"author": {"display_name": None}}, author("Li")]

    (publication,) = publications_from_works(
        {"results": [work(authorships=authorships)]}, ORCID, now=NOW
    )

    assert publication.authors == ()


def test_a_truncated_author_list_is_not_copied() -> None:
    # OpenAlex list endpoints return only the first 100 authorships (QA m1, #25).
    flagged = work(is_authors_truncated=True)
    at_limit = work(
        id="https://openalex.org/W2",
        doi="10.1234/big",
        authorships=[author("Jane Doe", ORCID)]
        + [author(f"Author {index}") for index in range(LIST_AUTHORSHIP_LIMIT - 1)],
    )
    below = work(
        id="https://openalex.org/W3",
        doi="10.1234/smaller",
        authorships=[author("Jane Doe", ORCID)]
        + [author(f"Author {index}") for index in range(LIST_AUTHORSHIP_LIMIT - 2)],
    )

    publications = publications_from_works({"results": [flagged, at_limit, below]}, ORCID, now=NOW)

    assert [len(p.authors) for p in publications] == [0, 0, LIST_AUTHORSHIP_LIMIT - 1]


def test_a_person_beyond_the_first_hundred_authorships_is_not_attributed() -> None:
    # Documented miss (ADR-020): the per-work re-check sees only the authorships
    # OpenAlex returned, so the work is dropped rather than attributed blindly.
    authorships = [author(f"Author {index}") for index in range(LIST_AUTHORSHIP_LIMIT)]

    payload = {"results": [work(authorships=authorships, is_authors_truncated=True)]}

    assert publications_from_works(payload, ORCID, now=NOW) == ()


@pytest.mark.parametrize("contact", ["", "   ", "not-an-email", None])
def test_the_wrapper_needs_a_contact_email(contact: object) -> None:
    with pytest.raises(ConfigError, match="RIB_RESEARCH_CONTACT_EMAIL"):
        OrcidPublicationsAdapter(
            Inner(()),
            FakeClient({}),  # type: ignore[arg-type]
            contact,  # type: ignore[arg-type]
        )


def test_a_work_without_a_title_or_any_link_is_skipped() -> None:
    payload = {
        "results": [
            work(display_name=None),
            work(display_name="x" * 1001),
            work(id=None, doi=None, primary_location={"landing_page_url": "ftp://x"}),
        ]
    }

    assert publications_from_works(payload, ORCID, now=NOW) == ()


def test_control_characters_and_oversized_venues_are_left_unknown() -> None:
    (publication,) = publications_from_works(
        {
            "results": [
                work(
                    primary_location={
                        "landing_page_url": "https://journal.example.org/graph",
                        "source": {"display_name": "v" * 501},
                    },
                    authorships=[author("Jane\x00Doe", ORCID)],
                )
            ]
        },
        ORCID,
        now=NOW,
    )

    assert publication.venue is None
    assert publication.authors == ()


@pytest.mark.parametrize("payload", [[], {"results": None}, {"results": "x"}, "text", None])
def test_a_malformed_response_raises(payload: object) -> None:
    with pytest.raises(DiscoveryError, match="OpenAlex"):
        publications_from_works(payload, ORCID, now=NOW)


def test_at_most_the_per_person_cap_is_kept() -> None:
    results = [
        work(id=f"https://openalex.org/W{index}", doi=f"10.1234/w{index}", display_name=f"W{index}")
        for index in range(WORKS_PER_PERSON + 5)
    ]

    assert len(publications_from_works({"results": results}, ORCID, now=NOW)) == WORKS_PER_PERSON


@pytest.mark.parametrize(
    ("index", "expected"),
    [
        ({"b": [1], "a": [0, 2]}, "a b a"),
        ({"a": [0], "c": [2]}, None),  # gap at position 1
        ({"a": [0], "b": [0]}, None),  # two words at one position
        ({"a": [-1]}, None),
        ({"a": ["0"]}, None),
        ({}, None),
        (None, None),
    ],
)
def test_abstract_reconstruction_is_exact_or_unknown(index: object, expected: str | None) -> None:
    assert abstract_from_inverted_index(index) == expected


# --------------------------------------------------------------------------- #
# the wrapping adapter
# --------------------------------------------------------------------------- #


def test_only_records_with_an_orcid_cause_a_request() -> None:
    adapter, client = adapter_for(
        (
            person(),
            person(
                full_name="Ravi Kumar",
                profile_url="https://www.cs.example.edu/people/ravi",
                email_source_url="https://www.cs.example.edu/people/ravi",
                orcid=None,
            ),
        ),
        {ORCID: {"results": [work()]}},
    )

    records = adapter.search(fac.FacultyQuery())

    assert len(client.calls) == 1
    assert client.accepts == ["application/json"]
    assert [len(record.publications) for record in records] == [1, 0]


def test_the_wrapper_keeps_the_inner_contact_authority() -> None:
    official, _ = adapter_for((), {})
    unofficial = OrcidPublicationsAdapter(
        Inner((), contact_authority="none"),
        FakeClient({}),  # type: ignore[arg-type]
        CONTACT,
    )

    assert official.contact_authority == "official"
    assert unofficial.contact_authority == "none"
    assert official.name == "official_faculty_page+openalex"


def test_an_inner_adapter_with_a_bad_authority_fails_at_construction() -> None:
    with pytest.raises(DiscoveryError, match="contact_authority"):
        OrcidPublicationsAdapter(
            Inner((), contact_authority="trusted"),
            FakeClient({}),  # type: ignore[arg-type]
            CONTACT,
        )


def test_a_bad_orcid_from_the_inner_adapter_never_reaches_the_network() -> None:
    adapter, client = adapter_for((person(orcid="0000-0002-1825-0098"),), {})

    with pytest.raises(DiscoveryError, match="not a valid ORCID"):
        adapter.search(fac.FacultyQuery())
    assert client.calls == []


def test_invalid_json_raises() -> None:
    adapter, _ = adapter_for((person(),), {ORCID: b"<html>not json</html>"})

    with pytest.raises(DiscoveryError, match="not valid JSON"):
        adapter.search(fac.FacultyQuery())


def test_existing_papers_are_kept_and_duplicates_collapse_in_validation() -> None:
    page_paper = fac.DiscoveredPublication(title="From page", doi="10.1234/graph.2025")
    adapter, _ = adapter_for(
        (person(publications=(page_paper,)),),
        {ORCID: {"results": [work(), work(id="W2", doi="10.1234/other", display_name="Other")]}},
    )

    (record,) = adapter.search(fac.FacultyQuery())
    validated = fac.validate_faculty(record, now=NOW)

    assert [p.title for p in record.publications] == [
        "From page",
        "Graph Learning for Proteins",
        "Other",
    ]
    assert [p.title for p in validated.publications] == ["From page", "Other"]


def test_end_to_end_papers_land_in_the_cache_and_a_repeat_is_free(tmp_path: Path) -> None:
    adapter, client = adapter_for(
        (person(),),
        {
            ORCID: {
                "results": [
                    work(),
                    work(
                        id="https://openalex.org/W200",
                        doi="10.1234/old",
                        display_name="Older Work",
                        publication_year=2019,
                    ),
                    work(
                        id="https://openalex.org/W300",
                        doi="10.1234/someone-else",
                        display_name="Not Hers",
                        authorships=[author("Jane Doe", OTHER_ORCID)],
                    ),
                ]
            }
        },
    )

    with db.session(tmp_path / "app.db") as connection:
        db.apply_migrations(connection)
        first = fac.search_faculty(
            connection, fac.FacultyQuery(country="Germany"), adapter=adapter, now=NOW
        )
        repeat = fac.search_faculty(
            connection, fac.FacultyQuery(country="germany"), adapter=adapter, now=NOW
        )

    (member,) = first.faculty
    assert [p.title for p in member.publications] == ["Graph Learning for Proteins", "Older Work"]
    assert [p.recent for p in member.publications] == [True, False]
    assert member.email_status == "verified"
    assert member.contact_sendable is True
    assert len(client.calls) == 1
    assert repeat.served_from_cache is True
