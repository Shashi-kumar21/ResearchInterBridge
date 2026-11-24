"""Explainable 0-100 matching between a student profile and cached research data.

Phase 6a (``task.md`` §3.6, ADR-022). Rules enforced here:

* **Deterministic keyword rules only.** A score is a pure function of the
  profile, the cached record, the weights and the evaluation date. No LLM, no
  embeddings, no network call; the same inputs always give the same score.
* **Hard gates are separate from the score.** A gate fails only when the source
  *states* a condition the student does not meet. A missing value on either side
  is ``unknown`` and is listed as a gap, never silently passed or failed.
  :func:`rank` always sorts an ineligible result after every other one, so a high
  similarity can never hide a failed mandatory condition.
* **Every point is explained.** Each factor carries its status, the points it
  earned and the exact terms that matched.
* **Private by construction.** This module reads a profile passed in by the
  caller and returns a value; it stores nothing. Persisting a snapshot belongs to
  the per-user shortlist (Phase 6b).
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date
from typing import Literal

from . import discovery
from .errors import ResearchInterBridgeError
from .faculty import FacultyMember
from .profiles import DegreeType, StudentProfile

#: Bump when a rule changes, so stored snapshots say which rules produced them.
SCORING_VERSION = "1"

#: Matched terms needed for full credit in a keyword factor. Saturating keeps a
#: student with many keywords from being penalised for listing them.
FULL_CREDIT_MATCHES = 3

#: A token shorter than this never matches on its own ("ai" still matches as a
#: whole phrase).
MIN_TOKEN_LENGTH = 3

FactorStatus = Literal["met", "partial", "unmet", "unknown", "not_applicable"]
GateStatus = Literal["pass", "fail", "unknown"]
Eligibility = Literal["eligible", "ineligible", "unknown", "not_assessed"]
TargetType = Literal["faculty", "program"]

_STOPWORDS = frozenset(
    {
        "and",
        "the",
        "for",
        "with",
        "from",
        "into",
        "via",
        "using",
        "based",
        "study",
        "studies",
        "research",
        "towards",
        "toward",
        "its",
        "their",
        "our",
        "new",
        "approach",
        "approaches",
        "method",
        "methods",
        "analysis",
    }
)
_NON_WORD = re.compile(r"[^\w+#]+", re.UNICODE)
_NO_FUNDING = re.compile(
    r"\b(unpaid|unfunded|self[- ]funded|none|no (stipend|funding|salary|payment|allowance)"
    r"|not (funded|paid|provided|available|offered|included))\b",
    re.I,
)

#: Student degree -> the levels a source may state for them (discovery tokens).
_STUDENT_LEVELS: Mapping[DegreeType, frozenset[str]] = {
    DegreeType.BTECH: frozenset({"bachelor"}),
    DegreeType.BE: frozenset({"bachelor"}),
    DegreeType.BS: frozenset({"bachelor"}),
    DegreeType.MSC: frozenset({"master"}),
    DegreeType.MTECH: frozenset({"master"}),
    DegreeType.DUAL: frozenset({"bachelor", "master"}),
    DegreeType.PHD: frozenset({"phd"}),
}
_KNOWN_LEVELS = frozenset({"bachelor", "master", "phd"})


class MatchingError(ResearchInterBridgeError):
    """Invalid matching input, such as negative weights."""

    code = "invalid_matching"


@dataclass(frozen=True, slots=True)
class MatchWeights:
    """Relative factor weights (Owner default, ADR-022). Need not sum to 100."""

    research_interest: float = 35
    publications: float = 20
    skills: float = 15
    timeline: float = 10
    preferences: float = 10
    recruitment: float = 5
    funding: float = 5

    def __post_init__(self) -> None:
        values = self.as_dict()
        for name, value in values.items():
            if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                raise MatchingError(f"weight {name!r} must be a non-negative number.")
        if sum(values.values()) <= 0:
            raise MatchingError("at least one weight must be positive.")

    def as_dict(self) -> dict[str, float]:
        return {
            "research_interest": self.research_interest,
            "publications": self.publications,
            "skills": self.skills,
            "timeline": self.timeline,
            "preferences": self.preferences,
            "recruitment": self.recruitment,
            "funding": self.funding,
        }


DEFAULT_WEIGHTS = MatchWeights()


@dataclass(frozen=True, slots=True)
class Factor:
    key: str
    label: str
    weight: float
    #: 0.0-1.0 share of the weight earned.
    credit: float
    status: FactorStatus
    reason: str
    matched: tuple[str, ...] = ()

    @property
    def points(self) -> float:
        return self.weight * self.credit


@dataclass(frozen=True, slots=True)
class Gate:
    key: str
    label: str
    status: GateStatus
    reason: str


@dataclass(frozen=True, slots=True)
class MatchResult:
    target_type: TargetType
    target_id: int
    target_name: str
    #: 0-100, over the weight of the factors that apply to this target type.
    score: int
    eligibility: Eligibility
    gates: tuple[Gate, ...]
    factors: tuple[Factor, ...]
    #: Unknowns a student should know about: missing profile values, unstated
    #: source values, stale records.
    gaps: tuple[str, ...]
    scoring_version: str
    #: Hash of the stored inputs (profile fields read, target id and last-check
    #: time, weights, scoring version). The evaluation date is not part of it:
    #: a snapshot is current while this matches *and* re-scoring today gives
    #: the same score and eligibility (a closing date can pass overnight).
    inputs_fingerprint: str
    evaluated_on: str = field(default="")


# --------------------------------------------------------------------------- #
# public API
# --------------------------------------------------------------------------- #


def score_faculty(
    profile: StudentProfile,
    member: FacultyMember,
    *,
    today: date,
    weights: MatchWeights = DEFAULT_WEIGHTS,
) -> MatchResult:
    """Score one cached faculty member for one student. Never stores anything.

    ``member`` must be a full load (:func:`faculty.get_faculty`, or
    :func:`faculty.browse_faculty` with ``include_publications=True``). A browse
    summary carries only highlighted papers, which would score the same person
    differently under the same fingerprint, so it is refused (QA B1 on #34).
    """

    if not member.publications and member.publication_count > 0:
        raise MatchingError(
            f"faculty {member.id} was loaded as a browse summary; score it from a full "
            "load (faculty.get_faculty or browse_faculty(include_publications=True))."
        )
    student_keywords = _phrases(profile.research_keywords)
    student_skills = _phrases((*profile.skills, *profile.coursework))
    interests = _phrases(member.research_interests)
    paper_titles = tuple(_phrases(publication.title for publication in member.publications))
    gaps: list[str] = []

    factors = (
        _keyword_factor(
            "research_interest",
            "Research-interest similarity",
            weights.research_interest,
            student_keywords,
            interests,
            student_missing="Your profile lists no research keywords.",
            source_missing="The official profile states no research interests.",
            gaps=gaps,
        ),
        _publication_factor(weights.publications, student_keywords, paper_titles, member, gaps),
        _keyword_factor(
            "skills",
            "Skills and coursework relevance",
            weights.skills,
            student_skills,
            (*interests, *paper_titles),
            student_missing="Your profile lists no skills or coursework.",
            source_missing="Neither interests nor papers are stated to compare skills with.",
            gaps=gaps,
        ),
        _not_applicable("timeline", "Internship timeline", weights.timeline, "faculty"),
        _country_factor(weights.preferences, profile, member.country, gaps),
        _faculty_recruitment(weights.recruitment, member, gaps),
        _not_applicable("funding", "Funding", weights.funding, "faculty"),
    )
    if member.stale:
        gaps.append("This faculty record is stale; it may have changed since it was checked.")
    return _result(
        "faculty",
        member.id,
        member.full_name,
        factors,
        gates=(),
        gaps=gaps,
        today=today,
        weights=weights,
        fingerprint_parts={
            "profile": _profile_inputs(profile),
            "target": {"id": member.id, "last_checked_at": member.last_checked_at},
        },
    )


def score_program(
    profile: StudentProfile,
    program: discovery.Program,
    *,
    today: date,
    weights: MatchWeights = DEFAULT_WEIGHTS,
) -> MatchResult:
    """Score one cached program for one student, with its hard gates."""

    student_keywords = _phrases(profile.research_keywords)
    student_skills = _phrases((*profile.skills, *profile.coursework))
    areas = _phrases(program.research_areas)
    eligibility_text = _phrases(program.eligibility)
    gaps: list[str] = []

    gates = (
        _degree_gate(profile, program),
        _status_gate(program, today),
    )
    for gate in gates:
        if gate.status == "unknown":
            gaps.append(gate.reason)

    factors = (
        _keyword_factor(
            "research_interest",
            "Research-interest similarity",
            weights.research_interest,
            student_keywords,
            (*areas, *_phrases((program.name,))),
            student_missing="Your profile lists no research keywords.",
            source_missing="The program states no research areas.",
            gaps=gaps,
        ),
        _not_applicable(
            "publications", "Publication-topic alignment", weights.publications, "program"
        ),
        _keyword_factor(
            "skills",
            "Skills and coursework relevance",
            weights.skills,
            student_skills,
            (*areas, *eligibility_text),
            student_missing="Your profile lists no skills or coursework.",
            source_missing="The program states no areas or eligibility to compare skills with.",
            gaps=gaps,
        ),
        _timeline_factor(weights.timeline, profile, program, gaps),
        _country_factor(weights.preferences, profile, program.country, gaps),
        _program_recruitment(weights.recruitment, program, gaps),
        _funding_factor(weights.funding, profile, program, gaps),
    )
    if program.stale:
        gaps.append("This program record is stale; dates and status may have changed.")
    return _result(
        "program",
        program.id,
        program.name,
        factors,
        gates=gates,
        gaps=gaps,
        today=today,
        weights=weights,
        fingerprint_parts={
            "profile": _profile_inputs(profile),
            "target": {"id": program.id, "last_checked_at": program.last_checked_at},
        },
    )


def rank(results: Iterable[MatchResult]) -> tuple[MatchResult, ...]:
    """Best first. An ineligible result always sorts after every other result."""

    return tuple(
        sorted(
            results,
            key=lambda result: (
                result.eligibility == "ineligible",
                -result.score,
                result.target_name.casefold(),
                result.target_id,
            ),
        )
    )


# --------------------------------------------------------------------------- #
# keyword matching
# --------------------------------------------------------------------------- #


def _fold_plural(token: str) -> str:
    """'networks' -> 'network'. Deliberately tiny: no stemmer, no dictionary."""

    if len(token) > 3 and token.endswith("s") and not token.endswith(("ss", "us", "is")):
        return token[:-1]
    return token


def _tokens(text: str) -> tuple[str, ...]:
    return tuple(_fold_plural(token) for token in _NON_WORD.split(text.casefold()) if token)


def _phrases(values: Iterable[str]) -> tuple[str, ...]:
    """Normalised, de-duplicated phrases, original order kept."""

    seen: dict[str, None] = {}
    for value in values:
        phrase = " ".join(_tokens(value))
        if phrase:
            seen.setdefault(phrase, None)
    return tuple(seen)


def _significant(tokens: Sequence[str]) -> frozenset[str]:
    return frozenset(
        token for token in tokens if len(token) >= MIN_TOKEN_LENGTH and token not in _STOPWORDS
    )


def _contains_phrase(haystack: Sequence[str], needle: Sequence[str]) -> bool:
    size = len(needle)
    return any(
        tuple(haystack[i : i + size]) == tuple(needle) for i in range(len(haystack) - size + 1)
    )


def _phrase_credit(student: str, targets: Sequence[str]) -> tuple[float, str | None]:
    """Credit for one student term against the source phrases.

    1.0 when the student's whole phrase appears in a source phrase; 0.5 when a
    shorter source phrase sits inside the student's phrase (an interest of just
    "learning" must not fully match "machine learning", QA n3 on #34) or when
    they share a significant word; else 0.
    """

    needle = student.split()
    for target in targets:
        if _contains_phrase(target.split(), needle):
            return 1.0, target
    for target in targets:
        hay = target.split()
        if _contains_phrase(needle, hay) and _significant(hay):
            return 0.5, target
    wanted = _significant(needle)
    if wanted:
        for target in targets:
            if wanted & _significant(target.split()):
                return 0.5, target
    return 0.0, None


def _keyword_factor(
    key: str,
    label: str,
    weight: float,
    student: Sequence[str],
    targets: Sequence[str],
    *,
    student_missing: str,
    source_missing: str,
    gaps: list[str],
) -> Factor:
    if not student:
        gaps.append(student_missing)
        return Factor(key, label, weight, 0.0, "unknown", student_missing)
    if not targets:
        gaps.append(source_missing)
        return Factor(key, label, weight, 0.0, "unknown", source_missing)
    total = 0.0
    matched: list[str] = []
    for phrase in student:
        credit, target = _phrase_credit(phrase, targets)
        if credit:
            total += credit
            matched.append(phrase if credit == 1.0 else f"{phrase} ~ {target}")
    credit = min(1.0, total / min(FULL_CREDIT_MATCHES, len(student)))
    if not matched:
        return Factor(key, label, weight, 0.0, "unmet", "None of your terms appear in the source.")
    status: FactorStatus = "met" if credit >= 1.0 else "partial"
    reason = f"Matched {len(matched)} of {len(student)} of your terms: {', '.join(matched)}."
    return Factor(key, label, weight, round(credit, 4), status, reason, tuple(matched))


def _publication_factor(
    weight: float,
    student: Sequence[str],
    titles: Sequence[str],
    member: FacultyMember,
    gaps: list[str],
) -> Factor:
    key, label = "publications", "Publication-topic alignment"
    if not student:
        return Factor(
            key, label, weight, 0.0, "unknown", "Your profile lists no research keywords."
        )
    if not titles:
        reason = (
            "No paper is attributed to this person (papers are linked only by a stated ORCID iD)."
        )
        gaps.append(reason)
        return Factor(key, label, weight, 0.0, "unknown", reason)
    aligned = [title for title in titles if any(_phrase_credit(p, (title,))[0] for p in student)]
    if not aligned:
        return Factor(key, label, weight, 0.0, "unmet", "No paper title shares a term with yours.")
    credit = min(1.0, len(aligned) / min(FULL_CREDIT_MATCHES, len(titles)))
    status: FactorStatus = "met" if credit >= 1.0 else "partial"
    reason = f"{len(aligned)} of {len(titles)} papers share a term with your keywords."
    return Factor(key, label, weight, round(credit, 4), status, reason, tuple(aligned[:5]))


# --------------------------------------------------------------------------- #
# other factors and gates
# --------------------------------------------------------------------------- #


def _not_applicable(key: str, label: str, weight: float, target: str) -> Factor:
    return Factor(key, label, weight, 0.0, "not_applicable", f"Does not apply to a {target}.")


def _country_factor(
    weight: float, profile: StudentProfile, country: str | None, gaps: list[str]
) -> Factor:
    key, label = "preferences", "Country preference"
    wanted = {" ".join(_tokens(value)) for value in profile.target_countries}
    if not wanted:
        reason = "Your profile lists no target countries."
        gaps.append(reason)
        return Factor(key, label, weight, 0.0, "unknown", reason)
    if not country:
        reason = "The source states no country."
        gaps.append(reason)
        return Factor(key, label, weight, 0.0, "unknown", reason)
    if " ".join(_tokens(country)) in wanted:
        return Factor(key, label, weight, 1.0, "met", f"{country} is a target country.", (country,))
    return Factor(
        key, label, weight, 0.0, "unmet", f"{country} is not one of your target countries."
    )


def _faculty_recruitment(weight: float, member: FacultyMember, gaps: list[str]) -> Factor:
    key, label = "recruitment", "Evidence of active recruitment"
    if member.recruitment_evidence_url:
        return Factor(
            key,
            label,
            weight,
            1.0,
            "met",
            "A source page states this person is recruiting.",
            (member.recruitment_evidence_url,),
        )
    reason = "No recruitment evidence is stated."
    gaps.append(reason)
    return Factor(key, label, weight, 0.0, "unknown", reason)


def _program_recruitment(weight: float, program: discovery.Program, gaps: list[str]) -> Factor:
    key, label = "recruitment", "Evidence of active recruitment"
    if program.status == "open":
        return Factor(key, label, weight, 1.0, "met", "The program is open per its source.")
    if program.status == "upcoming":
        return Factor(key, label, weight, 0.5, "partial", "The program opens later per its source.")
    if program.status == "closed":
        return Factor(key, label, weight, 0.0, "unmet", "The program is closed per its source.")
    reason = "The program's status is not verified."
    gaps.append(reason)
    return Factor(key, label, weight, 0.0, "unknown", reason)


def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _timeline_factor(
    weight: float, profile: StudentProfile, program: discovery.Program, gaps: list[str]
) -> Factor:
    key, label = "timeline", "Internship timeline"
    start = _parse_date(program.start_date)
    if profile.internship_start is None or profile.internship_end is None:
        reason = "Your profile does not state internship dates."
        gaps.append(reason)
        return Factor(key, label, weight, 0.0, "unknown", reason)
    if start is None:
        reason = "The program states no start date."
        gaps.append(reason)
        return Factor(key, label, weight, 0.0, "unknown", reason)
    if profile.internship_start <= start <= profile.internship_end:
        return Factor(
            key,
            label,
            weight,
            1.0,
            "met",
            f"Starts {start.isoformat()}, inside your window "
            f"{profile.internship_start.isoformat()} to {profile.internship_end.isoformat()}.",
        )
    return Factor(
        key,
        label,
        weight,
        0.0,
        "unmet",
        f"Starts {start.isoformat()}, outside your window "
        f"{profile.internship_start.isoformat()} to {profile.internship_end.isoformat()}.",
    )


def _funding_factor(
    weight: float, profile: StudentProfile, program: discovery.Program, gaps: list[str]
) -> Factor:
    key, label = "funding", "Funding"
    if profile.funding_required is None:
        reason = "Your profile does not say whether you need funding."
        gaps.append(reason)
        return Factor(key, label, weight, 0.0, "unknown", reason)
    if not profile.funding_required:
        return Factor(key, label, weight, 1.0, "met", "You do not need funding.")
    if program.stipend is None:
        reason = "You need funding, and the program states no stipend."
        gaps.append(reason)
        return Factor(key, label, weight, 0.0, "unknown", reason)
    if _NO_FUNDING.search(program.stipend):
        return Factor(
            key,
            label,
            weight,
            0.0,
            "unmet",
            f"You need funding; the source says: {program.stipend}.",
        )
    return Factor(
        key, label, weight, 1.0, "met", f"Stipend stated: {program.stipend}.", (program.stipend,)
    )


def _degree_gate(profile: StudentProfile, program: discovery.Program) -> Gate:
    key, label = "degree", "Degree level"
    stated = program.degree_level
    if stated is None:
        return Gate(key, label, "unknown", "The program states no degree level.")
    if stated not in _KNOWN_LEVELS:
        return Gate(key, label, "unknown", f"The stated level '{stated}' could not be compared.")
    if profile.degree_type is None:
        return Gate(key, label, "unknown", "Your profile does not state your degree.")
    levels = _STUDENT_LEVELS.get(profile.degree_type)
    if levels is None:
        return Gate(key, label, "unknown", "Your degree type cannot be compared with this level.")
    if stated in levels:
        return Gate(key, label, "pass", f"The program is for {stated} students, like you.")
    return Gate(
        key,
        label,
        "fail",
        f"The program states it is for {stated} students; your degree is "
        f"{profile.degree_type.value}.",
    )


def _status_gate(program: discovery.Program, today: date) -> Gate:
    key, label = "open", "Application window"
    closing = _parse_date(program.closing_date)
    if program.status == "closed":
        return Gate(key, label, "fail", "The source states the program is closed.")
    if closing is not None and closing < today:
        return Gate(
            key, label, "fail", f"The stated closing date {closing.isoformat()} has passed."
        )
    if program.status == "unverified" and closing is None:
        return Gate(key, label, "unknown", "Neither status nor closing date is verified.")
    return Gate(key, label, "pass", "The application window is not stated as closed.")


# --------------------------------------------------------------------------- #
# result assembly
# --------------------------------------------------------------------------- #


def _eligibility(gates: Sequence[Gate]) -> Eligibility:
    if not gates:
        return "not_assessed"
    if any(gate.status == "fail" for gate in gates):
        return "ineligible"
    if any(gate.status == "unknown" for gate in gates):
        return "unknown"
    return "eligible"


def _profile_inputs(profile: StudentProfile) -> dict[str, object]:
    """Only the profile fields a score reads; never stored on its own."""

    return {
        "research_keywords": list(profile.research_keywords),
        "skills": list(profile.skills),
        "coursework": list(profile.coursework),
        "target_countries": list(profile.target_countries),
        "degree_type": None if profile.degree_type is None else profile.degree_type.value,
        "internship_start": None
        if profile.internship_start is None
        else profile.internship_start.isoformat(),
        "internship_end": None
        if profile.internship_end is None
        else profile.internship_end.isoformat(),
        "funding_required": profile.funding_required,
    }


def _result(
    target_type: TargetType,
    target_id: int,
    target_name: str,
    factors: Sequence[Factor],
    *,
    gates: Sequence[Gate],
    gaps: Sequence[str],
    today: date,
    weights: MatchWeights,
    fingerprint_parts: Mapping[str, object],
) -> MatchResult:
    applicable = [factor for factor in factors if factor.status != "not_applicable"]
    possible = sum(factor.weight for factor in applicable)
    earned = sum(factor.points for factor in applicable)
    score = 0 if possible <= 0 else round(100 * earned / possible)
    fingerprint = hashlib.sha256(
        json.dumps(
            {
                "version": SCORING_VERSION,
                "type": target_type,
                "weights": weights.as_dict(),
                **fingerprint_parts,
            },
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    ).hexdigest()
    return MatchResult(
        target_type=target_type,
        target_id=target_id,
        target_name=target_name,
        score=max(0, min(100, score)),
        eligibility=_eligibility(gates),
        gates=tuple(gates),
        factors=tuple(factors),
        gaps=tuple(dict.fromkeys(gaps)),
        scoring_version=SCORING_VERSION,
        inputs_fingerprint=fingerprint,
        evaluated_on=today.isoformat(),
    )
