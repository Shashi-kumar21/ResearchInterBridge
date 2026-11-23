from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from research_inter_bridge import db, discovery
from research_inter_bridge import faculty as fac
from research_inter_bridge import research_cache as cache
from research_inter_bridge.errors import (
    DiscoveryError,
    DiscoveryUnavailableError,
    FacultyNotFoundError,
)

NOW = datetime(2026, 11, 5, 12, 0, tzinfo=UTC)
PROFILE = "https://www.cs.example.edu/people/jane.doe"


@pytest.fixture
def connection(tmp_path: Path):
    with db.session(tmp_path / "app.db") as conn:
        db.apply_migrations(conn)
        yield conn


def paper(**overrides: object) -> fac.DiscoveredPublication:
    base: dict[str, object] = {
        "title": "Graph Learning for Proteins",
        "doi": "10.1234/graph.2025",
        "year": 2025,
        "authors": ("Jane Doe", "Ravi Kumar"),
    }
    base.update(overrides)
    return fac.DiscoveredPublication(**base)  # type: ignore[arg-type]


def person(**overrides: object) -> fac.DiscoveredFaculty:
    base: dict[str, object] = {
        "full_name": "Jane Doe",
        "institution": "Example University",
        "country": "Germany",
        "department": "Computer Science",
        "profile_url": PROFILE,
        "research_interests": ("machine learning", "bioinformatics"),
        "email": "jane.doe@cs.example.edu",
        "email_source_url": PROFILE,
        "publications": (paper(),),
        "confidence": 0.9,
    }
    base.update(overrides)
    return fac.DiscoveredFaculty(**base)  # type: ignore[arg-type]


@dataclass
class FakeAdapter:
    records: tuple[fac.DiscoveredFaculty, ...] = ()
    name: str = "fixture"
    parser_version: str = "1"
    # Most tests model the operator-configured official-page source; the
    # contact-authority tests below override this.
    contact_authority: str = "official"
    calls: int = 0
    error: Exception | None = None
    queries: list[fac.FacultyQuery] = field(default_factory=list)

    def search(self, query: fac.FacultyQuery) -> tuple[fac.DiscoveredFaculty, ...]:
        self.calls += 1
        self.queries.append(query)
        if self.error is not None:
            raise self.error
        return self.records


def fill(
    connection: sqlite3.Connection,
    *records: fac.DiscoveredFaculty,
    query: fac.FacultyQuery | None = None,
    now: datetime = NOW,
) -> fac.FacultyResult:
    return fac.search_faculty(
        connection,
        query or fac.FacultyQuery(country="Germany"),
        adapter=FakeAdapter(records=records),
        now=now,
    )


def audit_rows(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    return connection.execute(
        "SELECT action, outcome, metadata_json FROM audit_events ORDER BY id"
    ).fetchall()


# --------------------------------------------------------------------------- #
# schema
# --------------------------------------------------------------------------- #


def test_faculty_tables_are_public_and_carry_no_user_id(connection: sqlite3.Connection) -> None:
    for table in (
        "faculty_members",
        "faculty_contacts",
        "faculty_sources",
        "publications",
        "publication_authors",
        "faculty_publications",
    ):
        columns = {row["name"] for row in connection.execute(f"PRAGMA table_info({table})")}
        assert columns, table
        assert "user_id" not in columns, table


def test_a_contact_status_outside_the_allowed_set_is_rejected_by_the_schema(
    connection: sqlite3.Connection,
) -> None:
    fill(connection, person())
    faculty_id = connection.execute("SELECT id FROM faculty_members").fetchone()["id"]
    connection.execute("DELETE FROM faculty_contacts")
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute(
            "INSERT INTO faculty_contacts (faculty_id, kind, value, source_url, status) "
            "VALUES (?, 'email', 'a@b.edu', ?, 'guessed')",
            (faculty_id, PROFILE),
        )


# --------------------------------------------------------------------------- #
# validation
# --------------------------------------------------------------------------- #


def test_validation_cleans_text_and_keeps_unknowns_unknown() -> None:
    record = fac.validate_faculty(
        person(full_name="  Jane   Doe ", title=None, lab_name=None, country=None), now=NOW
    )

    assert record.full_name == "Jane Doe"
    assert record.title is None
    assert record.lab_name is None
    assert record.country is None
    assert record.sources[0].url == PROFILE


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"email_source_url": None}, "no email_source_url"),
        ({"email": None}, "but no email"),
        ({"email": "jane [at] cs.example.edu"}, "single plain address"),
        ({"email": "jane@cs.example.edu, bob@cs.example.edu"}, "single plain address"),
        ({"email": "jane@localhost"}, "single plain address"),
        ({"profile_url": "ftp://cs.example.edu/jane"}, "http(s) URL"),
        ({"full_name": "   "}, "must not be blank"),
        ({"confidence": 1.5}, "expected 0.0-1.0"),
        ({"confidence": True}, "non-numeric"),
        ({"http_status": 700}, "HTTP status"),
        ({"recruitment_evidence_url": "javascript:alert(1)"}, "http(s) URL"),
    ],
)
def test_validation_rejects_malformed_faculty(overrides: dict[str, object], fragment: str) -> None:
    with pytest.raises(DiscoveryError) as excinfo:
        fac.validate_faculty(person(**overrides), now=NOW)

    assert fragment in str(excinfo.value)


def test_an_email_domain_is_lower_cased_but_the_local_part_is_kept() -> None:
    record = fac.validate_faculty(person(email=" Jane.Doe@CS.Example.EDU "), now=NOW)

    assert record.email == "Jane.Doe@cs.example.edu"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("10.1234/ABC.def", "10.1234/abc.def"),
        ("https://doi.org/10.1234/abc", "10.1234/abc"),
        ("http://dx.doi.org/10.1234/abc", "10.1234/abc"),
        ("doi: 10.1234/abc", "10.1234/abc"),
        (None, None),
    ],
)
def test_doi_normalisation(raw: str | None, expected: str | None) -> None:
    assert fac.normalise_doi(raw) == expected


@pytest.mark.parametrize("raw", ["", "11.1234/abc", "10.12/abc", "https://example.org/paper"])
def test_a_malformed_doi_is_rejected(raw: str) -> None:
    with pytest.raises(DiscoveryError):
        fac.normalise_doi(raw)


@pytest.mark.parametrize(
    ("overrides", "fragment"),
    [
        ({"doi": None, "url": None}, "neither a URL nor a DOI"),
        ({"year": 2028}, "expected 1800-2027"),
        ({"year": 1700}, "expected 1800-2027"),
        ({"year": "2025"}, "non-integer year"),
        ({"year": True}, "non-integer year"),
        ({"authors": "Jane Doe"}, "not a string"),
        ({"abstract": "x" * (fac.MAX_ABSTRACT_LENGTH + 1)}, "abstract over"),
        ({"url": "mailto:jane@cs.example.edu"}, "http(s) URL"),
    ],
)
def test_validation_rejects_malformed_publications(
    overrides: dict[str, object], fragment: str
) -> None:
    with pytest.raises(DiscoveryError) as excinfo:
        fac.validate_publication(paper(**overrides), now=NOW)

    assert fragment in str(excinfo.value)


def test_a_doi_without_a_url_links_to_the_doi_resolver() -> None:
    record = fac.validate_publication(paper(url=None, doi="10.1234/X"), now=NOW)

    assert record.url == "https://doi.org/10.1234/x"


def test_authors_keep_their_order_and_duplicate_names() -> None:
    record = fac.validate_publication(paper(authors=("Li Wei", "Jane Doe", "Li Wei")), now=NOW)

    assert record.authors == ("Li Wei", "Jane Doe", "Li Wei")


def test_the_same_doi_twice_in_one_record_is_one_publication() -> None:
    record = fac.validate_faculty(
        person(publications=(paper(), paper(doi="https://doi.org/10.1234/GRAPH.2025"))), now=NOW
    )

    assert len(record.publications) == 1


def test_too_many_publications_are_rejected() -> None:
    papers = tuple(
        paper(doi=f"10.1234/p{index}", title=f"P{index}")
        for index in range(fac.MAX_PUBLICATIONS + 1)
    )
    with pytest.raises(DiscoveryError, match="publications"):
        fac.validate_faculty(person(publications=papers), now=NOW)


# --------------------------------------------------------------------------- #
# identity and contact verification
# --------------------------------------------------------------------------- #


def test_people_with_the_same_name_are_never_merged(connection: sqlite3.Connection) -> None:
    other = "https://www.cs.example.edu/people/jane-doe"

    result = fill(connection, person(), person(profile_url=other, email_source_url=other))

    assert {member.profile_url for member in result.faculty} == {PROFILE, other}
    assert len({member.id for member in result.faculty}) == 2


def test_the_profile_url_not_the_name_is_the_identity(connection: sqlite3.Connection) -> None:
    first = fill(connection, person()).faculty[0]
    renamed = fill(
        connection,
        person(
            full_name="Jane Doe-Smith", profile_url="http://cs.example.edu/people/jane.doe/#bio"
        ),
        query=fac.FacultyQuery(country="germany", research_area="machine"),
        now=NOW + timedelta(minutes=1),
    ).faculty[0]

    assert renamed.id == first.id
    assert renamed.full_name == "Jane Doe-Smith"
    assert connection.execute("SELECT COUNT(*) FROM faculty_members").fetchone()[0] == 1


def test_profile_url_normalisation_keeps_path_and_query() -> None:
    assert fac.normalise_profile_url("HTTPS://WWW.X.edu/a/") == "//x.edu/a"
    assert fac.normalise_profile_url("http://x.edu/a#top") == "//x.edu/a"
    assert fac.normalise_profile_url("https://x.edu/p?id=1") != fac.normalise_profile_url(
        "https://x.edu/p?id=2"
    )


def test_an_email_listed_on_the_official_host_is_verified_and_sendable(
    connection: sqlite3.Connection,
) -> None:
    member = fill(
        connection, person(email_source_url="https://cs.example.edu/people/jane.doe/contact")
    ).faculty[0]

    assert member.email == "jane.doe@cs.example.edu"
    assert member.email_status == "verified"
    assert member.contact_sendable is True


def test_an_email_listed_elsewhere_is_unverified_and_never_sendable(
    connection: sqlite3.Connection,
) -> None:
    member = fill(connection, person(email_source_url="https://janedoe.github.io/")).faculty[0]

    assert member.email_status == "unverified"
    assert member.contact_sendable is False
    assert member.email_source_url == "https://janedoe.github.io/"
    row = connection.execute("SELECT status, verified_at FROM faculty_contacts").fetchone()
    assert (row["status"], row["verified_at"]) == ("unverified", None)


@pytest.mark.parametrize(
    ("profile", "listing"),
    [
        # An ORCID record lists a self-reported address on its own host.
        ("https://orcid.org/0000-0002-1825-0097", "https://orcid.org/0000-0002-1825-0097"),
        # A shared host: another person's page would match too.
        ("https://sites.google.com/view/jane", "https://sites.google.com/view/other"),
        # Even an institutional host, when the adapter is not an official source.
        (PROFILE, PROFILE),
    ],
)
def test_a_non_official_adapter_never_verifies_an_address_on_any_host(
    connection: sqlite3.Connection, profile: str, listing: str
) -> None:
    adapter = FakeAdapter(
        records=(person(profile_url=profile, email_source_url=listing),),
        contact_authority="none",
    )

    member = fac.search_faculty(
        connection, fac.FacultyQuery(country="Germany"), adapter=adapter, now=NOW
    ).faculty[0]

    assert member.email == "jane.doe@cs.example.edu"
    assert member.email_status == "unverified"
    assert member.contact_sendable is False


def test_an_adapter_that_declares_no_authority_is_treated_as_none(
    connection: sqlite3.Connection,
) -> None:
    @dataclass
    class Undeclared:
        name: str = "aggregator"
        parser_version: str = "1"

        def search(self, query: fac.FacultyQuery) -> tuple[fac.DiscoveredFaculty, ...]:
            return (person(),)

    member = fac.search_faculty(
        connection, fac.FacultyQuery(country="Germany"), adapter=Undeclared(), now=NOW
    ).faculty[0]

    assert member.email_status == "unverified"
    assert member.contact_sendable is False


@pytest.mark.parametrize("authority", ["Official", "trusted", "", None])
def test_an_unknown_authority_fails_before_the_source_is_called(
    connection: sqlite3.Connection, authority: object
) -> None:
    adapter = FakeAdapter(records=(person(),), contact_authority=authority)  # type: ignore[arg-type]

    with pytest.raises(DiscoveryError, match="contact_authority"):
        fac.search_faculty(connection, fac.FacultyQuery(country="Germany"), adapter=adapter)
    assert adapter.calls == 0


@pytest.mark.parametrize(
    ("listing", "authority", "expected"),
    [
        (PROFILE, "official", "verified"),
        ("https://cs.example.edu/people/jane.doe/contact", "official", "verified"),
        ("https://example.edu/directory", "official", "unverified"),
        (PROFILE, "none", "unverified"),
    ],
)
def test_email_status_needs_official_authority_and_the_profile_host(
    listing: str, authority: str, expected: str
) -> None:
    assert fac.email_status_for(listing, PROFILE, authority=authority) == expected


def test_email_status_rejects_an_unknown_authority() -> None:
    with pytest.raises(DiscoveryError, match="contact authority"):
        fac.email_status_for(PROFILE, PROFILE, authority="trusted")


def test_a_profile_without_an_email_is_unavailable(connection: sqlite3.Connection) -> None:
    member = fill(connection, person(email=None, email_source_url=None)).faculty[0]

    assert member.email is None
    assert member.email_status == "unavailable"
    assert member.contact_sendable is False


def test_a_stale_profile_is_not_sendable_even_with_a_verified_email(
    connection: sqlite3.Connection,
) -> None:
    fill(connection)
    member_id = fill(connection, person(), query=fac.FacultyQuery(country="x")).faculty[0].id

    later = fac.get_faculty(connection, member_id, now=NOW + timedelta(days=31))

    assert later.email_status == "verified"
    assert later.stale is True
    assert later.contact_sendable is False


def test_a_refresh_drops_an_address_the_page_no_longer_lists(
    connection: sqlite3.Connection,
) -> None:
    fill(connection, person())
    adapter = FakeAdapter(records=(person(email=None, email_source_url=None),))

    result = fac.search_faculty(
        connection,
        fac.FacultyQuery(country="Germany"),
        adapter=adapter,
        now=NOW + timedelta(days=31),
    )

    assert adapter.calls == 1
    assert result.faculty[0].email is None
    assert connection.execute("SELECT COUNT(*) FROM faculty_contacts").fetchone()[0] == 0


# --------------------------------------------------------------------------- #
# publications
# --------------------------------------------------------------------------- #


def test_publications_are_newest_first_with_three_highlighted(
    connection: sqlite3.Connection,
) -> None:
    papers = (
        paper(title="Old", doi="10.1234/old", year=2015),
        paper(title="Undated", doi="10.1234/undated", year=None),
        paper(title="New", doi="10.1234/new", year=2026),
        paper(title="Mid", doi="10.1234/mid", year=2024),
        paper(title="Recent", doi="10.1234/recent", year=2025),
    )

    member = fill(connection, person(publications=papers)).faculty[0]

    assert [p.title for p in member.publications] == ["New", "Recent", "Mid", "Old", "Undated"]
    assert [p.title for p in member.highlighted_publications] == ["New", "Recent", "Mid"]
    assert [p.recent for p in member.publications] == [True, True, True, False, False]


def test_a_publication_keeps_its_source_link_authors_and_unknown_fields(
    connection: sqlite3.Connection,
) -> None:
    member = fill(
        connection,
        person(publications=(paper(url="https://arxiv.org/abs/2501.00001", venue=None),)),
    ).faculty[0]
    publication = member.publications[0]

    assert publication.url == "https://arxiv.org/abs/2501.00001"
    assert publication.doi == "10.1234/graph.2025"
    assert publication.authors == ("Jane Doe", "Ravi Kumar")
    assert publication.venue is None
    assert publication.abstract is None
    assert publication.stale is False
    assert publication.last_checked_at == cache.to_timestamp(NOW)


def test_a_paper_is_linked_only_to_the_profile_it_was_attributed_to(
    connection: sqlite3.Connection,
) -> None:
    # A second Jane Doe co-authors nothing here: the shared author name on the
    # first profile's paper must not attach that paper to her.
    other = "https://www.bio.example.edu/staff/jdoe"
    result = fill(
        connection,
        person(),
        person(
            profile_url=other,
            email_source_url=other,
            department="Biology",
            publications=(),
        ),
    )
    by_url = {member.profile_url: member for member in result.faculty}

    assert [p.title for p in by_url[PROFILE].publications] == ["Graph Learning for Proteins"]
    assert by_url[other].publications == ()


def test_co_authors_share_one_publication_row(connection: sqlite3.Connection) -> None:
    other = "https://www.cs.example.edu/people/ravi.kumar"

    result = fill(
        connection,
        person(),
        person(full_name="Ravi Kumar", profile_url=other, email_source_url=other),
    )

    assert {member.publications[0].id for member in result.faculty} == {
        result.faculty[0].publications[0].id
    }
    assert connection.execute("SELECT COUNT(*) FROM publications").fetchone()[0] == 1


def test_a_url_identified_paper_ignores_scheme_and_trailing_slash(
    connection: sqlite3.Connection,
) -> None:
    other = "https://www.cs.example.edu/people/ravi.kumar"
    fill(
        connection,
        person(publications=(paper(doi=None, url="http://lab.example.edu/papers/42"),)),
        person(
            full_name="Ravi Kumar",
            profile_url=other,
            email_source_url=other,
            publications=(paper(doi=None, url="https://lab.example.edu/papers/42/"),),
        ),
    )

    assert connection.execute("SELECT COUNT(*) FROM publications").fetchone()[0] == 1


def test_publications_get_their_own_cache_entries(connection: sqlite3.Connection) -> None:
    member = fill(connection, person()).faculty[0]

    entry = cache.entries_for_entity(
        connection, entity_type="publication", entity_id=member.publications[0].id
    )[0]
    assert entry.ttl_seconds == cache.TTL_PUBLICATION_SECONDS
    assert entry.adapter == "fixture"


# --------------------------------------------------------------------------- #
# cache-first search
# --------------------------------------------------------------------------- #


def test_a_fresh_repeat_query_makes_zero_adapter_calls(connection: sqlite3.Connection) -> None:
    fill(connection, person(), query=fac.FacultyQuery(research_area="Machine Learning"))
    adapter = FakeAdapter(records=(person(),))

    result = fac.search_faculty(
        connection,
        fac.FacultyQuery(research_area="  machine learning "),
        adapter=adapter,
        now=NOW + timedelta(days=1),
    )

    assert adapter.calls == 0
    assert result.served_from_cache is True
    assert result.stale is False
    assert [member.full_name for member in result.faculty] == ["Jane Doe"]


def test_the_adapter_receives_a_normalised_query(connection: sqlite3.Connection) -> None:
    adapter = FakeAdapter(records=())

    fac.search_faculty(
        connection,
        fac.FacultyQuery(research_area=" Machine  Learning", institution="Example UNIVERSITY"),
        adapter=adapter,
        now=NOW,
    )

    assert adapter.queries == [
        fac.FacultyQuery(research_area="machine learning", institution="example university")
    ]


def test_an_empty_answer_is_cached_for_a_day(connection: sqlite3.Connection) -> None:
    adapter = FakeAdapter(records=())
    query = fac.FacultyQuery(research_area="astrobiology")

    first = fac.search_faculty(connection, query, adapter=adapter, now=NOW)
    second = fac.search_faculty(connection, query, adapter=adapter, now=NOW + timedelta(hours=23))
    fac.search_faculty(connection, query, adapter=adapter, now=NOW + timedelta(hours=25))

    assert first.faculty == ()
    assert second.served_from_cache is True
    assert adapter.calls == 2


def test_an_expired_entry_refreshes_once(connection: sqlite3.Connection) -> None:
    fill(connection, person())
    adapter = FakeAdapter(records=(person(title="Professor"),))

    result = fac.search_faculty(
        connection, fac.FacultyQuery(country="Germany"), adapter=adapter, now=NOW + timedelta(31)
    )

    assert adapter.calls == 1
    assert result.served_from_cache is False
    assert result.faculty[0].title == "Professor"
    assert result.faculty[0].stale is False


def test_a_failed_refresh_keeps_rows_and_marks_every_backing_entry_stale(
    connection: sqlite3.Connection,
) -> None:
    member = fill(connection, person()).faculty[0]
    later = NOW + timedelta(days=31)
    adapter = FakeAdapter(error=RuntimeError("boom"))

    result = fac.search_faculty(
        connection, fac.FacultyQuery(country="Germany"), adapter=adapter, now=later
    )

    assert result.stale is True
    assert result.served_from_cache is True
    assert result.faculty[0].id == member.id
    assert result.faculty[0].email == "jane.doe@cs.example.edu"
    assert result.faculty[0].contact_sendable is False
    assert "do not use their contact details" in (result.message or "")
    for entity_type, entity_id in (
        ("faculty", member.id),
        ("publication", member.publications[0].id),
        ("institution", member.institution_id),
        ("department", member.department_id),
    ):
        entries = cache.entries_for_entity(connection, entity_type=entity_type, entity_id=entity_id)
        assert entries[0].last_error == "RuntimeError", entity_type
    failure = [row for row in audit_rows(connection) if row["action"] == "research.refresh_failed"]
    assert len(failure) == 1
    metadata = json.loads(failure[0]["metadata_json"])
    assert metadata["entity"] == "faculty"
    assert metadata["error"] == "RuntimeError"
    assert "boom" not in failure[0]["metadata_json"]


def test_a_failed_source_is_not_retried_within_the_hour(connection: sqlite3.Connection) -> None:
    fill(connection, person())
    later = NOW + timedelta(days=31)
    adapter = FakeAdapter(error=RuntimeError("down"))
    query = fac.FacultyQuery(country="Germany")

    fac.search_faculty(connection, query, adapter=adapter, now=later)
    backed_off = fac.search_faculty(
        connection, query, adapter=adapter, now=later + timedelta(minutes=30)
    )
    assert adapter.calls == 1
    assert backed_off.stale is True
    assert "not retried yet" in (backed_off.message or "")

    fac.search_faculty(connection, query, adapter=adapter, now=later + timedelta(hours=2))
    assert adapter.calls == 2


def test_a_cold_failure_raises_a_discovery_error(connection: sqlite3.Connection) -> None:
    adapter = FakeAdapter(error=RuntimeError("down"))

    with pytest.raises(DiscoveryError, match="failed \\(RuntimeError\\)"):
        fac.search_faculty(connection, fac.FacultyQuery(country="Germany"), adapter=adapter)


def test_an_invalid_record_fails_the_whole_refresh(connection: sqlite3.Connection) -> None:
    adapter = FakeAdapter(records=(person(), person(email_source_url=None)))

    with pytest.raises(DiscoveryError, match="no email_source_url"):
        fac.search_faculty(connection, fac.FacultyQuery(country="Germany"), adapter=adapter)
    assert connection.execute("SELECT COUNT(*) FROM faculty_members").fetchone()[0] == 0


def test_no_adapter_and_no_cache_is_unavailable(connection: sqlite3.Connection) -> None:
    with pytest.raises(DiscoveryUnavailableError):
        fac.search_faculty(connection, fac.FacultyQuery(country="Germany"))


def test_no_adapter_serves_expired_rows_as_stale(connection: sqlite3.Connection) -> None:
    fill(connection, person())

    result = fac.search_faculty(
        connection, fac.FacultyQuery(country="Germany"), now=NOW + timedelta(days=31)
    )

    assert result.stale is True
    assert result.faculty[0].stale is True


def test_offline_filters_local_rows_and_never_calls_an_adapter(
    connection: sqlite3.Connection,
) -> None:
    other = "https://www.physik.example.de/team/max"
    fill(
        connection,
        person(),
        person(
            full_name="Max Weber",
            institution="Beispiel Universität",
            department="Physics",
            profile_url=other,
            email_source_url=other,
            research_interests=("quantum optics",),
        ),
    )
    adapter = FakeAdapter(records=(person(),))

    by_area = fac.search_faculty(
        connection,
        fac.FacultyQuery(research_area="quantum"),
        adapter=adapter,
        offline=True,
        now=NOW,
    )
    by_institution = fac.search_faculty(
        connection,
        fac.FacultyQuery(institution="beispiel"),
        offline=True,
        now=NOW,
    )
    nothing = fac.search_faculty(
        connection, fac.FacultyQuery(country="Japan"), offline=True, now=NOW
    )

    assert adapter.calls == 0
    assert [member.full_name for member in by_area.faculty] == ["Max Weber"]
    assert [member.full_name for member in by_institution.faculty] == ["Max Weber"]
    assert nothing.faculty == ()
    assert "Run the query online once" in (nothing.message or "")


def test_an_unknown_field_never_matches_a_stated_filter() -> None:
    record = fac.validate_faculty(person(country=None, department=None), now=NOW)

    assert fac.discovered_faculty_matches(record, fac.FacultyQuery(country="germany")) is False
    assert fac.discovered_faculty_matches(record, fac.FacultyQuery(department="computer")) is False
    assert fac.discovered_faculty_matches(record, fac.FacultyQuery(research_area="machine"))


def test_faculty_and_program_fingerprints_never_collide() -> None:
    faculty_print = fac.FacultyQuery(research_area="ml", country="germany").fingerprint()
    program_print = discovery.ProgramQuery(research_area="ml", country="germany").fingerprint()

    assert faculty_print != program_print
    assert faculty_print.startswith("faculty-query:")


def test_the_served_audit_names_the_entity_and_carries_no_contact(
    connection: sqlite3.Connection,
) -> None:
    fill(connection, person())

    rows = audit_rows(connection)
    served = json.loads(rows[-1]["metadata_json"])

    assert rows[-1]["action"] == "research.query_served"
    assert served["entity"] == "faculty"
    assert served["faculty"] == 1
    assert all("jane.doe@" not in row["metadata_json"] for row in rows)


# --------------------------------------------------------------------------- #
# browse hierarchy
# --------------------------------------------------------------------------- #


def test_university_department_faculty_browse_works_without_a_network(
    connection: sqlite3.Connection,
) -> None:
    other = "https://www.cs.example.edu/people/ada"
    biology = "https://www.bio.example.edu/staff/li"
    fill(
        connection,
        person(),
        person(full_name="Ada Byron", profile_url=other, email_source_url=other),
        person(
            full_name="Li Wei",
            department="Biology",
            profile_url=biology,
            email_source_url=biology,
        ),
    )

    institution = discovery.browse_institutions(connection, now=NOW).institutions[0]
    departments = discovery.browse_departments(connection, institution.id, now=NOW).departments
    computer_science = next(d for d in departments if d.name == "Computer Science")
    page = fac.browse_faculty(
        connection,
        fac.FacultyFilters(institution_id=institution.id, department_id=computer_science.id),
        now=NOW,
    )
    everyone = fac.browse_faculty(
        connection, fac.FacultyFilters(institution_id=institution.id), now=NOW
    )

    assert institution.name == "Example University"
    assert institution.stale is False
    assert sorted(d.name for d in departments) == ["Biology", "Computer Science"]
    assert [member.full_name for member in page.faculty] == ["Ada Byron", "Jane Doe"]
    assert page.total == 2
    assert everyone.total == 3


def test_programs_and_faculty_share_one_institution_row(connection: sqlite3.Connection) -> None:
    fill(connection, person())
    discovery.search_programs(
        connection,
        discovery.ProgramQuery(country="Germany"),
        adapter=_ProgramAdapter(),
        now=NOW,
    )

    institutions = discovery.browse_institutions(connection, now=NOW)

    assert institutions.total == 1
    assert institutions.institutions[0].program_count == 1


def test_browse_pages_filters_and_validates_bounds(connection: sqlite3.Connection) -> None:
    records = [
        person(
            full_name=f"Person {index}",
            profile_url=f"https://www.cs.example.edu/people/{index}",
            email_source_url=f"https://www.cs.example.edu/people/{index}",
            research_interests=("robotics",) if index % 2 else ("vision",),
            publications=(),
        )
        for index in range(5)
    ]
    fill(connection, *records)

    page = fac.browse_faculty(connection, limit=2, offset=2, now=NOW)
    robotics = fac.browse_faculty(connection, fac.FacultyFilters(research_area="Robotics"), now=NOW)

    assert [member.full_name for member in page.faculty] == ["Person 2", "Person 3"]
    assert page.total == 5
    assert robotics.total == 2
    with pytest.raises(DiscoveryError):
        fac.browse_faculty(connection, limit=0)
    with pytest.raises(DiscoveryError):
        fac.browse_faculty(connection, fac.FacultyFilters(institution_id=0))


def test_reads_are_batched_under_a_low_parameter_limit(
    connection: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    records = [
        person(
            full_name=f"Person {index}",
            profile_url=f"https://www.cs.example.edu/people/{index}",
            email_source_url=f"https://www.cs.example.edu/people/{index}",
            publications=(paper(doi=f"10.1234/p{index}", title=f"Paper {index}"),),
        )
        for index in range(7)
    ]
    fill(connection, *records)
    monkeypatch.setattr(discovery, "READ_BATCH_SIZE", 2)

    page = fac.browse_faculty(connection, limit=100, now=NOW)

    assert page.total == 7
    assert all(len(member.publications) == 1 for member in page.faculty)
    assert all(member.email_status == "verified" for member in page.faculty)


def test_get_faculty_and_a_missing_id(connection: sqlite3.Connection) -> None:
    member = fill(connection, person()).faculty[0]

    assert fac.get_faculty(connection, member.id, now=NOW).full_name == "Jane Doe"
    with pytest.raises(FacultyNotFoundError) as excinfo:
        fac.get_faculty(connection, member.id + 100)
    assert excinfo.value.code == "faculty_missing"


@dataclass
class _ProgramAdapter:
    name: str = "fixture"
    parser_version: str = "1"

    def search(self, query: discovery.ProgramQuery) -> tuple[discovery.DiscoveredProgram, ...]:
        return (
            discovery.DiscoveredProgram(
                name="MSc Research Internship",
                institution="Example University",
                country="Germany",
                official_url="https://www.example.edu/programs/research-internship",
            ),
        )


# --------------------------------------------------------------------------- #
# ORCID (Phase 5b)
# --------------------------------------------------------------------------- #

ORCID = "0000-0002-1825-0097"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        (ORCID, ORCID),
        (f"https://orcid.org/{ORCID}", ORCID),
        (f"http://orcid.org/{ORCID}/", ORCID),
        (f"https://www.orcid.org/{ORCID}", ORCID),
        ("0000-0002-9079-593x", "0000-0002-9079-593X"),
        (None, None),
    ],
)
def test_orcid_normalisation(raw: str | None, expected: str | None) -> None:
    assert fac.normalise_orcid(raw) == expected


@pytest.mark.parametrize(
    "raw", ["0000-0002-1825-0098", "0000000218250097", "orcid", "https://example.org/0000"]
)
def test_an_invalid_orcid_is_rejected(raw: str) -> None:
    with pytest.raises(DiscoveryError, match="not a valid ORCID"):
        fac.normalise_orcid(raw)


def test_an_orcid_is_stored_and_refreshed_with_the_profile(connection: sqlite3.Connection) -> None:
    member = fill(connection, person(orcid=f"https://orcid.org/{ORCID}")).faculty[0]
    refreshed = fac.search_faculty(
        connection,
        fac.FacultyQuery(country="Germany"),
        adapter=FakeAdapter(records=(person(),)),
        now=NOW + timedelta(days=31),
    ).faculty[0]

    assert member.orcid == ORCID
    assert refreshed.id == member.id
    assert refreshed.orcid is None


def test_an_invalid_orcid_fails_validation() -> None:
    with pytest.raises(DiscoveryError, match="not a valid ORCID"):
        fac.validate_faculty(person(orcid="0000-0002-1825-0098"), now=NOW)
