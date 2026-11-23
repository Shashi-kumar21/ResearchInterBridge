from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from research_inter_bridge import db
from research_inter_bridge import faculty as fac
from research_inter_bridge.errors import DiscoveryError
from research_inter_bridge.faculty_pages import (
    MAX_PEOPLE_PER_PAGE,
    FacultyPageSource,
    OfficialPageFacultyAdapter,
    faculty_from_response,
    load_faculty_pages,
)
from research_inter_bridge.http_source import SourceResponse

NOW = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
DIRECTORY = "https://www.cs.example.edu/people"
SOURCE = FacultyPageSource(
    url=DIRECTORY,
    institution="Example University",
    country="Germany",
    department="Computer Science",
)
#: The public ORCID documentation example; its check digit is valid.
ORCID = "0000-0002-1825-0097"


def response(html: str, status: int = 200, url: str = DIRECTORY) -> SourceResponse:
    return SourceResponse(
        status=status,
        url=url,
        content_type="text/html; charset=utf-8",
        body=html.encode("utf-8"),
    )


def json_ld(*payloads: object) -> str:
    blocks = "".join(
        '<script type="application/ld+json">' + json.dumps(payload) + "</script>"
        for payload in payloads
    )
    return f"<html><head>{blocks}</head><body></body></html>"


def jane(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "@context": "https://schema.org",
        "@type": "Person",
        "name": "Jane Doe",
        "jobTitle": "Professor of Machine Learning",
        "url": "https://www.cs.example.edu/people/jane.doe",
        "email": "mailto:jane.doe@cs.example.edu",
        "knowsAbout": ["machine learning", "bioinformatics"],
        "sameAs": [f"https://orcid.org/{ORCID}", "https://janedoe.github.io/"],
    }
    base.update(overrides)
    return base


class FakeClient:
    """One prepared response per URL; no socket is opened."""

    def __init__(self, responses: dict[str, SourceResponse]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    def get(self, url: str) -> SourceResponse:
        self.calls.append(url)
        return self.responses[url]


# --------------------------------------------------------------------------- #
# parsing
# --------------------------------------------------------------------------- #


def test_reads_only_explicit_person_fields() -> None:
    (person,) = faculty_from_response(SOURCE, response(json_ld(jane())))

    assert person.full_name == "Jane Doe"
    assert person.title == "Professor of Machine Learning"
    assert person.institution == "Example University"
    assert person.country == "Germany"
    assert person.department == "Computer Science"
    assert person.profile_url == "https://www.cs.example.edu/people/jane.doe"
    assert person.email == "jane.doe@cs.example.edu"
    assert person.email_source_url == DIRECTORY
    assert person.orcid == ORCID
    assert person.research_interests == ("machine learning", "bioinformatics")
    assert person.publications == ()
    assert person.lab_name is None
    assert person.recruitment_evidence_url is None
    assert person.confidence is None
    assert person.http_status == 200
    assert [(s.url, s.kind) for s in person.sources] == [
        ("https://www.cs.example.edu/people/jane.doe", "official_page"),
        (DIRECTORY, "directory_page"),
    ]


def test_a_person_without_a_url_uses_the_page_as_the_profile() -> None:
    (person,) = faculty_from_response(SOURCE, response(json_ld(jane(url=None))))

    assert person.profile_url == DIRECTORY
    assert [s.url for s in person.sources] == [DIRECTORY]


@pytest.mark.parametrize(
    "email",
    [
        "jane [at] cs.example.edu",
        "jane.doe(at)cs.example.edu",
        "jane.doe@cs.example.edu, bob@cs.example.edu",
        "",
        None,
        ["jane.doe@cs.example.edu"],
    ],
)
def test_an_obfuscated_or_missing_email_stays_unknown(email: object) -> None:
    (person,) = faculty_from_response(SOURCE, response(json_ld(jane(email=email))))

    assert person.email is None
    assert person.email_source_url is None


def test_no_email_is_derived_from_the_name_or_the_host() -> None:
    payload = jane()
    del payload["email"]

    (person,) = faculty_from_response(SOURCE, response(json_ld(payload)))

    # The page names the person and the host, but states no address: none is made up.
    assert person.email is None
    assert person.email_source_url is None


def test_mailto_query_parameters_are_dropped() -> None:
    (person,) = faculty_from_response(
        SOURCE, response(json_ld(jane(email="mailto:jane.doe@cs.example.edu?subject=Hi")))
    )

    assert person.email == "jane.doe@cs.example.edu"


@pytest.mark.parametrize(
    ("same_as", "identifier", "expected"),
    [
        ([f"https://orcid.org/{ORCID}"], None, ORCID),
        (None, {"@type": "PropertyValue", "propertyID": "ORCID", "value": ORCID}, ORCID),
        (None, [{"propertyID": "orcid", "value": f"https://orcid.org/{ORCID}"}], ORCID),
        (["https://orcid.org/0000-0002-1825-0098"], None, None),  # bad check digit
        (["https://scholar.example.org/jane"], None, None),
        ([ORCID], None, None),  # a bare number is not stated as an ORCID
        (None, {"propertyID": "staff-number", "value": ORCID}, None),
    ],
)
def test_orcid_is_read_only_when_stated_and_valid(
    same_as: object, identifier: object, expected: str | None
) -> None:
    (person,) = faculty_from_response(
        SOURCE, response(json_ld(jane(sameAs=same_as, identifier=identifier)))
    )

    assert person.orcid == expected


def test_given_and_family_name_are_joined_only_when_both_are_stated() -> None:
    both = jane(name=None, givenName="Ada", familyName="Byron")
    only_given = jane(name=None, givenName="Ada", url="https://www.cs.example.edu/people/ada-only")

    people = faculty_from_response(SOURCE, response(json_ld(both, only_given)))

    assert [person.full_name for person in people] == ["Ada Byron"]


def test_a_profile_page_main_entity_is_read_once() -> None:
    person = jane()
    payload = {
        "@context": "https://schema.org",
        "@graph": [
            {"@type": "ProfilePage", "mainEntity": person},
            person,
        ],
    }

    people = faculty_from_response(SOURCE, response(json_ld(payload)))

    assert [p.full_name for p in people] == ["Jane Doe"]


def test_a_directory_page_lists_several_people_and_skips_other_types() -> None:
    payload = {
        "@graph": [
            jane(),
            jane(name="Ravi Kumar", url="https://www.cs.example.edu/people/ravi", email=None),
            {"@type": "Organization", "name": "Example University"},
            {"@type": "EducationalOccupationalProgram", "name": "MSc"},
        ]
    }

    people = faculty_from_response(SOURCE, response(json_ld(payload)))

    assert [p.full_name for p in people] == ["Jane Doe", "Ravi Kumar"]
    assert people[1].email is None


def test_the_configured_institution_wins_over_works_for() -> None:
    record = jane(worksFor={"@type": "Organization", "name": "Machine Learning Lab"})

    (configured,) = faculty_from_response(SOURCE, response(json_ld(record)))
    (fallback,) = faculty_from_response(
        FacultyPageSource(url=DIRECTORY, institution=" "), response(json_ld(record))
    )

    assert configured.institution == "Example University"
    assert fallback.institution == "Machine Learning Lab"


def test_a_malformed_block_does_not_hide_a_valid_one() -> None:
    html = '<script type="application/ld+json">{not json</script>' + json_ld(jane())

    people = faculty_from_response(SOURCE, response(html))

    assert [p.full_name for p in people] == ["Jane Doe"]


def test_a_nameless_person_is_skipped() -> None:
    people = faculty_from_response(SOURCE, response(json_ld(jane(name=None))))

    assert people == ()


def test_one_page_yields_at_most_the_cap() -> None:
    graph = [
        jane(name=f"Person {index}", url=f"https://www.cs.example.edu/people/{index}")
        for index in range(MAX_PEOPLE_PER_PAGE + 5)
    ]

    people = faculty_from_response(SOURCE, response(json_ld({"@graph": graph})))

    assert len(people) == MAX_PEOPLE_PER_PAGE


def test_parsed_records_pass_faculty_validation() -> None:
    people = faculty_from_response(
        SOURCE, response(json_ld({"@graph": [jane(), jane(name="X", url=None, email=None)]}))
    )

    for person in people:
        fac.validate_faculty(person, now=NOW)


# --------------------------------------------------------------------------- #
# loading and the adapter
# --------------------------------------------------------------------------- #


def test_pages_are_read_sequentially_once_each() -> None:
    second = FacultyPageSource(url="https://www.bio.example.edu/staff", institution="Example U")
    client = FakeClient(
        {
            DIRECTORY: response(json_ld(jane())),
            second.url: response(json_ld(jane(url=None)), url=second.url),
        }
    )

    pages = load_faculty_pages(client, (SOURCE, second))  # type: ignore[arg-type]

    assert client.calls == [DIRECTORY, second.url]
    assert [len(page.faculty) for page in pages] == [1, 1]


def test_a_failed_page_raises_instead_of_returning_a_partial_result() -> None:
    class FailingClient(FakeClient):
        def get(self, url: str) -> SourceResponse:
            self.calls.append(url)
            if url != DIRECTORY:
                raise DiscoveryError("host down")
            return self.responses[url]

    second = FacultyPageSource(url="https://www.bio.example.edu/staff", institution="Example U")
    client = FailingClient({DIRECTORY: response(json_ld(jane()))})

    with pytest.raises(DiscoveryError, match="host down"):
        load_faculty_pages(client, (SOURCE, second))  # type: ignore[arg-type]


def test_the_adapter_applies_the_shared_faculty_filter() -> None:
    payload = {
        "@graph": [
            jane(),
            jane(
                name="Max Weber",
                url="https://www.cs.example.edu/people/max",
                knowsAbout="quantum optics, photonics",
            ),
        ]
    }
    adapter = OfficialPageFacultyAdapter(
        FakeClient({DIRECTORY: response(json_ld(payload))}),  # type: ignore[arg-type]
        (SOURCE,),
    )

    quantum = adapter.search(fac.FacultyQuery(research_area="quantum").normalised())
    japan = adapter.search(fac.FacultyQuery(country="japan").normalised())

    assert [person.full_name for person in quantum] == ["Max Weber"]
    assert japan == ()


def test_the_adapter_needs_a_configured_page() -> None:
    with pytest.raises(DiscoveryError, match="at least one configured source URL"):
        OfficialPageFacultyAdapter(FakeClient({}), ())  # type: ignore[arg-type]


def test_end_to_end_into_the_cache_with_verification(tmp_path: Path) -> None:
    payload = {
        "@graph": [
            jane(),
            # Listed on a different host than its profile: kept, but unverified.
            jane(
                name="Li Wei",
                url="https://www.bio.example.org/staff/li",
                email="li.wei@bio.example.org",
                sameAs=None,
            ),
        ]
    }
    client = FakeClient({DIRECTORY: response(json_ld(payload))})
    adapter = OfficialPageFacultyAdapter(client, (SOURCE,))  # type: ignore[arg-type]

    with db.session(tmp_path / "app.db") as connection:
        db.apply_migrations(connection)
        result = fac.search_faculty(
            connection, fac.FacultyQuery(country="Germany"), adapter=adapter, now=NOW
        )
        repeat = fac.search_faculty(
            connection, fac.FacultyQuery(country="germany"), adapter=adapter, now=NOW
        )

    by_name = {member.full_name: member for member in result.faculty}
    assert by_name["Jane Doe"].email_status == "verified"
    assert by_name["Jane Doe"].contact_sendable is True
    assert by_name["Jane Doe"].orcid == ORCID
    assert by_name["Li Wei"].email_status == "unverified"
    assert by_name["Li Wei"].contact_sendable is False
    assert by_name["Li Wei"].orcid is None
    assert client.calls == [DIRECTORY]
    assert repeat.served_from_cache is True
