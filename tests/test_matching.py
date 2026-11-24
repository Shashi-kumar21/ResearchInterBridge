from __future__ import annotations

from dataclasses import replace
from datetime import date

import pytest

from research_inter_bridge import discovery
from research_inter_bridge import matching as m
from research_inter_bridge.faculty import FacultyMember, Publication
from research_inter_bridge.profiles import DegreeType, StudentProfile

TODAY = date(2026, 11, 24)


def profile(**overrides: object) -> StudentProfile:
    base: dict[str, object] = {
        "user_id": 1,
        "full_name": "Asha Rao",
        "contact_email": "asha@example.edu",
        "degree_type": DegreeType.MSC,
        "research_keywords": ("machine learning", "graph neural networks", "proteins"),
        "skills": ("Python", "PyTorch"),
        "coursework": ("Bioinformatics",),
        "target_countries": ("Germany",),
        "internship_start": date(2027, 5, 1),
        "internship_end": date(2027, 8, 31),
        "funding_required": True,
    }
    base.update(overrides)
    return StudentProfile(**base)  # type: ignore[arg-type]


def paper(pid: int, title: str, year: int = 2025) -> Publication:
    return Publication(
        id=pid,
        canonical_key=f"doi:10.1/{pid}",
        title=title,
        year=year,
        doi=f"10.1/{pid}",
        url=f"https://doi.org/10.1/{pid}",
        venue=None,
        abstract=None,
        authors=(),
        recent=True,
        last_checked_at="2026-11-20T00:00:00+00:00",
        stale=False,
    )


def member(**overrides: object) -> FacultyMember:
    papers = (
        paper(1, "Graph Neural Networks for Protein Folding"),
        paper(2, "Machine Learning on Molecules"),
        paper(3, "A Survey of Compilers", 2019),
    )
    base: dict[str, object] = {
        "id": 10,
        "canonical_key": "faculty:abc",
        "full_name": "Jane Doe",
        "institution": "Example University",
        "institution_id": 1,
        "department": "Computer Science",
        "department_id": 2,
        "country": "Germany",
        "title": "Professor",
        "lab_name": None,
        "profile_url": "https://cs.example.edu/jane",
        "evidence_urls": ("https://cs.example.edu/jane",),
        "research_interests": ("Machine Learning", "Computational Biology", "Protein design"),
        "email": None,
        "email_status": "unavailable",
        "email_source_url": None,
        "orcid": "0000-0002-1825-0097",
        "contact_sendable": False,
        "recruitment_evidence_url": "https://cs.example.edu/jane/openings",
        "publications": papers,
        "highlighted_publications": papers[:3],
        "publication_count": len(papers),
        "confidence": 0.9,
        "last_checked_at": "2026-11-20T00:00:00+00:00",
        "stale": False,
    }
    base.update(overrides)
    return FacultyMember(**base)  # type: ignore[arg-type]


def program(**overrides: object) -> discovery.Program:
    base: dict[str, object] = {
        "id": 20,
        "canonical_key": "program:x",
        "name": "Summer Research Internship in Machine Learning",
        "institution": "Example University",
        "department": "Computer Science",
        "institution_id": 1,
        "department_id": 2,
        "country": "Germany",
        "opportunity_type": "research internship",
        "degree_level": "master",
        "research_areas": ("machine learning", "bioinformatics"),
        "official_url": "https://example.edu/summer",
        "evidence_urls": ("https://example.edu/summer",),
        "eligibility": ("Enrolled in a master's programme", "Python experience"),
        "application_method": None,
        "opening_date": "2026-11-01",
        "closing_date": "2027-01-15",
        "start_date": "2027-06-01",
        "duration": "3 months",
        "stipend": "EUR 1,200 per month",
        "tuition": None,
        "accommodation": None,
        "required_documents": (),
        "status": "open",
        "confidence": 0.8,
        "last_checked_at": "2026-11-20T00:00:00+00:00",
        "stale": False,
    }
    base.update(overrides)
    return discovery.Program(**base)  # type: ignore[arg-type]


def factor(result: m.MatchResult, key: str) -> m.Factor:
    return next(item for item in result.factors if item.key == key)


def gate(result: m.MatchResult, key: str) -> m.Gate:
    return next(item for item in result.gates if item.key == key)


# --------------------------------------------------------------------------- #
# faculty
# --------------------------------------------------------------------------- #


def test_a_strong_faculty_match_explains_every_point() -> None:
    result = m.score_faculty(profile(), member(), today=TODAY)

    research = factor(result, "research_interest")
    assert research.status == "partial"
    assert "machine learning" in research.matched
    # "proteins" folds to "protein", a whole phrase inside "protein design".
    assert "protein" in research.matched
    pubs = factor(result, "publications")
    assert pubs.status == "partial"
    assert pubs.credit == pytest.approx(2 / 3, abs=1e-4)
    assert factor(result, "preferences").status == "met"
    assert factor(result, "recruitment").status == "met"
    assert factor(result, "timeline").status == "not_applicable"
    assert factor(result, "funding").status == "not_applicable"
    assert result.eligibility == "not_assessed"
    assert 0 < result.score <= 100
    assert result.scoring_version == m.SCORING_VERSION


def test_not_applicable_factors_do_not_cap_a_faculty_score() -> None:
    perfect = member(
        research_interests=("machine learning", "graph neural networks", "proteins", "python"),
        publications=(
            paper(1, "Machine learning a"),
            paper(2, "Graph neural networks b"),
            paper(3, "Proteins c"),
        ),
    )

    result = m.score_faculty(profile(skills=("python",), coursework=()), perfect, today=TODAY)

    # Timeline and funding do not apply to a person, so 100 is reachable.
    assert result.score == 100


def test_a_whole_phrase_counts_fully_and_a_shared_word_half() -> None:
    result = m.score_faculty(
        profile(research_keywords=("protein design",)),
        member(research_interests=("protein engineering",)),
        today=TODAY,
    )
    exact = m.score_faculty(
        profile(research_keywords=("protein design",)),
        member(research_interests=("Protein Design",)),
        today=TODAY,
    )

    assert factor(result, "research_interest").credit == 0.5
    assert factor(result, "research_interest").matched == ("protein design ~ protein engineering",)
    assert factor(exact, "research_interest").credit == 1.0


def test_plurals_fold_but_other_words_stay_distinct() -> None:
    result = m.score_faculty(
        profile(research_keywords=("graph neural networks", "analysis")),
        member(research_interests=("Graph Neural Network", "Analytics")),
        today=TODAY,
    )

    assert factor(result, "research_interest").matched == ("graph neural network",)


def test_stopwords_and_short_tokens_never_match_alone() -> None:
    result = m.score_faculty(
        profile(research_keywords=("research methods", "ai")),
        member(research_interests=("Research ethics", "AI safety")),
        today=TODAY,
    )

    research = factor(result, "research_interest")
    # "research" is a stopword, so "research methods" earns nothing; "ai" is a
    # whole phrase inside "ai safety", so it still counts.
    assert research.matched == ("ai",)


def test_missing_values_are_unknown_gaps_not_failures() -> None:
    result = m.score_faculty(
        profile(research_keywords=(), target_countries=()),
        member(
            research_interests=(),
            publications=(),
            highlighted_publications=(),
            recruitment_evidence_url=None,
            country=None,
        ),
        today=TODAY,
    )

    assert factor(result, "research_interest").status == "unknown"
    assert factor(result, "publications").status == "unknown"
    assert factor(result, "preferences").status == "unknown"
    assert factor(result, "recruitment").status == "unknown"
    assert result.score == 0
    assert "Your profile lists no research keywords." in result.gaps
    assert "Your profile lists no target countries." in result.gaps
    assert "No recruitment evidence is stated." in result.gaps
    assert len(result.gaps) == len(set(result.gaps))


def test_a_stale_faculty_record_is_a_gap() -> None:
    result = m.score_faculty(profile(), member(stale=True), today=TODAY)

    assert any("stale" in gap for gap in result.gaps)


# --------------------------------------------------------------------------- #
# programs and gates
# --------------------------------------------------------------------------- #


def test_an_eligible_program_passes_both_gates() -> None:
    result = m.score_program(profile(), program(), today=TODAY)

    assert result.eligibility == "eligible"
    assert gate(result, "degree").status == "pass"
    assert gate(result, "open").status == "pass"
    assert factor(result, "timeline").status == "met"
    assert factor(result, "funding").status == "met"
    assert factor(result, "publications").status == "not_applicable"


def test_a_stated_degree_mismatch_fails_the_gate() -> None:
    result = m.score_program(profile(degree_type=DegreeType.BTECH), program(), today=TODAY)

    assert gate(result, "degree").status == "fail"
    assert result.eligibility == "ineligible"
    assert "btech" in gate(result, "degree").reason


@pytest.mark.parametrize(
    ("degree_level", "degree_type"),
    [
        (None, DegreeType.MSC),
        ("Graduate certificate", DegreeType.MSC),
        ("master", None),
        ("master", DegreeType.OTHER),
    ],
)
def test_an_unstated_or_uncomparable_degree_is_unknown(
    degree_level: str | None, degree_type: DegreeType | None
) -> None:
    result = m.score_program(
        profile(degree_type=degree_type), program(degree_level=degree_level), today=TODAY
    )

    assert gate(result, "degree").status == "unknown"
    assert result.eligibility == "unknown"
    assert gate(result, "degree").reason in result.gaps


def test_a_dual_degree_student_fits_bachelor_and_master_levels() -> None:
    for level in ("bachelor", "master"):
        result = m.score_program(
            profile(degree_type=DegreeType.DUAL), program(degree_level=level), today=TODAY
        )
        assert gate(result, "degree").status == "pass"


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"status": "closed"}, "fail"),
        ({"closing_date": "2026-11-01"}, "fail"),
        ({"status": "unverified", "closing_date": None}, "unknown"),
        ({"status": "upcoming"}, "pass"),
    ],
)
def test_the_application_window_gate(overrides: dict[str, object], expected: str) -> None:
    result = m.score_program(profile(), program(**overrides), today=TODAY)

    assert gate(result, "open").status == expected


def test_timeline_and_funding_cover_known_unknown_and_unmet() -> None:
    late = m.score_program(profile(), program(start_date="2027-10-01"), today=TODAY)
    no_dates = m.score_program(
        profile(internship_start=None, internship_end=None), program(), today=TODAY
    )
    unpaid = m.score_program(profile(), program(stipend="Unpaid"), today=TODAY)
    no_stipend = m.score_program(profile(), program(stipend=None), today=TODAY)
    self_funded = m.score_program(
        profile(funding_required=False), program(stipend=None), today=TODAY
    )

    assert factor(late, "timeline").status == "unmet"
    assert factor(no_dates, "timeline").status == "unknown"
    assert factor(unpaid, "funding").status == "unmet"
    assert factor(no_stipend, "funding").status == "unknown"
    assert "You need funding, and the program states no stipend." in no_stipend.gaps
    assert factor(self_funded, "funding").status == "met"


# --------------------------------------------------------------------------- #
# ranking, weights, fingerprint
# --------------------------------------------------------------------------- #


def test_an_ineligible_result_ranks_below_every_eligible_one_despite_its_score() -> None:
    strong_but_closed = m.score_program(profile(), program(id=1, status="closed"), today=TODAY)
    weak_but_open = m.score_program(
        profile(),
        program(id=2, name="Unrelated", research_areas=("history",), eligibility=(), stipend=None),
        today=TODAY,
    )
    assert strong_but_closed.score > weak_but_open.score

    ranked = m.rank([strong_but_closed, weak_but_open])

    assert [result.target_id for result in ranked] == [2, 1]


def test_weights_change_the_score_and_are_validated() -> None:
    default = m.score_faculty(profile(), member(), today=TODAY)
    research_only = m.score_faculty(
        profile(),
        member(),
        today=TODAY,
        weights=m.MatchWeights(
            research_interest=1,
            publications=0,
            skills=0,
            timeline=0,
            preferences=0,
            recruitment=0,
            funding=0,
        ),
    )

    assert research_only.score == round(100 * factor(default, "research_interest").credit)
    assert research_only.inputs_fingerprint != default.inputs_fingerprint
    with pytest.raises(m.MatchingError):
        m.MatchWeights(skills=-1)
    with pytest.raises(m.MatchingError):
        m.MatchWeights(0, 0, 0, 0, 0, 0, 0)


def test_the_score_is_deterministic_and_the_fingerprint_tracks_inputs() -> None:
    first = m.score_faculty(profile(), member(), today=TODAY)
    again = m.score_faculty(profile(), member(), today=TODAY)
    new_keywords = m.score_faculty(profile(research_keywords=("compilers",)), member(), today=TODAY)
    rechecked = m.score_faculty(
        profile(), member(last_checked_at="2026-11-23T00:00:00+00:00"), today=TODAY
    )

    assert first == again
    assert (
        len(
            {
                first.inputs_fingerprint,
                new_keywords.inputs_fingerprint,
                rechecked.inputs_fingerprint,
            }
        )
        == 3
    )


def test_a_private_field_never_reaches_the_fingerprint_inputs() -> None:
    inputs = m._profile_inputs(profile(phone="+49 111", gpa=3.9, gpa_scale=4.0))

    for private in ("full_name", "contact_email", "phone", "gpa", "user_id"):
        assert private not in inputs


def test_matching_does_not_mutate_its_inputs() -> None:
    student, person = profile(), member()
    before = (replace(student), replace(person))

    m.score_faculty(student, person, today=TODAY)

    assert (student, person) == before
