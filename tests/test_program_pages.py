from __future__ import annotations

import json

from research_inter_bridge.discovery import MAX_ITEMS
from research_inter_bridge.http_source import SourceResponse
from research_inter_bridge.program_pages import (
    PROGRAM_SCHEMA_TYPES,
    ProgramPageSource,
    load_program_page,
    load_program_pages,
    programs_from_response,
)

SOURCE = ProgramPageSource(
    url="https://example.edu/programs",
    institution="Example University",
    country="Germany",
    department="Physics",
)


def response(html: str, status: int = 200) -> SourceResponse:
    return SourceResponse(
        status=status,
        url=SOURCE.url,
        content_type="text/html; charset=utf-8",
        body=html.encode("utf-8"),
    )


def json_ld(payload: object) -> str:
    return (
        '<html><head><script type="application/ld+json">'
        + json.dumps(payload)
        + "</script></head><body></body></html>"
    )


def test_reads_only_explicit_schema_fields_and_leaves_unknowns_unknown() -> None:
    payload = {
        "@context": "https://schema.org",
        "@type": "EducationalOccupationalProgram",
        "name": "MSc Research Internship",
        "provider": {"@type": "CollegeOrUniversity", "name": "Example University"},
        "url": "https://example.edu/programs/research-internship",
        "applicationStartDate": "2026-12-01",
        "applicationDeadline": "2027-01-15T23:59:00Z",
        "startDate": "2027-03-01",
        "timeToComplete": "P6M",
        "keywords": "machine learning, physics",
        "programPrerequisites": "Enrolled in a master's programme",
        "educationalCredentialAwarded": "Master's degree",
    }

    (program,) = programs_from_response(SOURCE, response(json_ld(payload)))

    assert program.name == "MSc Research Internship"
    assert program.institution == "Example University"
    assert program.official_url == "https://example.edu/programs/research-internship"
    assert program.country == "Germany"
    assert program.department == "Physics"
    assert program.opening_date == "2026-12-01"
    assert program.closing_date == "2027-01-15"
    assert program.start_date == "2027-03-01"
    assert program.duration == "P6M"
    assert program.research_areas == ("machine learning", "physics")
    assert program.eligibility == ("Enrolled in a master's programme",)
    assert program.degree_level == "Master's degree"
    assert program.status == "unverified"
    assert program.confidence is None
    assert program.stipend is None
    assert program.tuition is None
    assert program.accommodation is None
    assert program.application_method is None
    assert program.required_documents == ()
    assert program.http_status == 200
    assert program.sources[0].url == SOURCE.url
    assert program.sources[0].publisher == "Example University"
    assert program.sources[0].kind == "official_page"


def test_reads_a_job_posting_and_its_explicit_employment_type() -> None:
    payload = {
        "@type": "JobPosting",
        "name": "Summer Research Assistant",
        "hiringOrganization": {"@type": "Organization", "name": "Example Lab"},
        "employmentType": "INTERN",
        "keywords": ["robotics", "robotics", " control "],
        "programPrerequisites": [
            {"@type": "EducationalOccupationalCredential", "name": "BSc in engineering"},
            "Available for 10 weeks",
        ],
        "applicationDeadline": "not a date",
    }

    (program,) = programs_from_response(SOURCE, response(json_ld(payload)))

    assert program.institution == "Example Lab"
    assert program.opportunity_type == "INTERN"
    assert program.research_areas == ("robotics", "control")
    assert program.eligibility == ("BSc in engineering", "Available for 10 weeks")
    assert program.closing_date is None


def test_flattens_a_graph_and_skips_unsupported_or_incomplete_items() -> None:
    payload = {
        "@context": "https://schema.org",
        "@graph": [
            {"@type": "Course", "name": "Not a research placement"},
            {"@type": "JobPosting", "provider": {"name": "Example University"}},
            {"@type": "EducationalOccupationalProgram", "name": "Valid program"},
        ],
    }

    (program,) = programs_from_response(SOURCE, response(json_ld(payload)))

    assert program.name == "Valid program"
    assert program.official_url == SOURCE.url  # no URL stated, so the page is the source


def test_malformed_block_does_not_hide_a_valid_block_next_to_it() -> None:
    html = (
        '<script type="application/ld+json">{not json</script>'
        '<script type="application/ld+json">'
        + json.dumps({"@type": "EducationalOccupationalProgram", "name": "Valid"})
        + "</script>"
    )

    (program,) = programs_from_response(SOURCE, response(html))

    assert program.name == "Valid"


def test_a_page_without_structured_data_yields_nothing() -> None:
    html = "<html><body><h1>Apply for our internship</h1><p>Deadline soon.</p></body></html>"

    assert programs_from_response(SOURCE, response(html)) == ()


def test_non_http_url_falls_back_to_the_configured_page() -> None:
    payload = {
        "@type": "EducationalOccupationalProgram",
        "name": "Program",
        "url": "javascript:alert(1)",
    }

    (program,) = programs_from_response(SOURCE, response(json_ld(payload)))

    assert program.official_url == SOURCE.url


def test_invalid_iso_dates_stay_unknown() -> None:
    payload = {
        "@type": "EducationalOccupationalProgram",
        "name": "Program",
        "applicationDeadline": "15 January 2027",
        "startDate": "2027-13-01",
    }

    (program,) = programs_from_response(SOURCE, response(json_ld(payload)))

    assert program.closing_date is None
    assert program.start_date is None


def test_result_is_capped_at_the_shared_item_limit() -> None:
    payload = {
        "@graph": [
            {"@type": "EducationalOccupationalProgram", "name": f"Program {index}"}
            for index in range(MAX_ITEMS + 5)
        ]
    }

    programs = programs_from_response(SOURCE, response(json_ld(payload)))

    assert len(programs) == MAX_ITEMS


def test_load_program_page_uses_one_polite_get() -> None:
    class FakeClient:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def get(self, url: str) -> SourceResponse:
            self.calls.append(url)
            return response(json_ld({"@type": "EducationalOccupationalProgram", "name": "Program"}))

    client = FakeClient()
    page = load_program_page(client, SOURCE)  # type: ignore[arg-type]

    assert client.calls == [SOURCE.url]
    assert page.source is SOURCE
    assert page.http_status == 200
    assert page.programs[0].name == "Program"


def test_load_program_pages_reads_in_order_and_keeps_each_source() -> None:
    second = ProgramPageSource(
        url="https://second.example.edu/programs",
        institution="Second University",
    )

    class FakeClient:
        def __init__(self) -> None:
            self.calls: list[str] = []

        def get(self, url: str) -> SourceResponse:
            self.calls.append(url)
            name = "First" if url == SOURCE.url else "Second"
            return SourceResponse(
                status=200,
                url=url,
                content_type="text/html",
                body=json_ld({"@type": "EducationalOccupationalProgram", "name": name}).encode(
                    "utf-8"
                ),
            )

    client = FakeClient()
    pages = load_program_pages(client, (SOURCE, second))  # type: ignore[arg-type]

    assert client.calls == [SOURCE.url, second.url]
    assert [page.programs[0].name for page in pages] == ["First", "Second"]


def test_supported_schema_types_are_named_not_inferred() -> None:
    assert {"EducationalOccupationalProgram", "JobPosting"} == PROGRAM_SCHEMA_TYPES
    assert "Course" not in PROGRAM_SCHEMA_TYPES
