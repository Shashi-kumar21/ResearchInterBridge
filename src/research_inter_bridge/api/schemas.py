"""Request and response bodies for the JSON API.

Rules enforced here:

* Every request model forbids unknown fields, so a misspelt key fails loudly
  instead of being ignored (a typo must never look like consent).
* The password is a plain :class:`str` on purpose. Password policy lives in
  :func:`research_inter_bridge.auth.validate_password`, and keeping it out of the
  schema means a rejected password is never echoed back in a validation error.
* Response models carry no password, hash, token hash or filesystem path.
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class _StrictModel(BaseModel):
    """Base model that rejects unknown fields."""

    model_config = ConfigDict(extra="forbid")


class RegisterRequest(_StrictModel):
    email: str
    password: str
    consents: list[str] = Field(default_factory=list)


class LoginRequest(_StrictModel):
    email: str
    password: str


class ConsentRequest(_StrictModel):
    kind: str


class DeleteAccountRequest(_StrictModel):
    password: str


class AccountOut(BaseModel):
    user_id: int
    email: str
    created_at: str


class SessionOut(BaseModel):
    account: AccountOut
    expires_at: str


class ConsentStateOut(BaseModel):
    kind: str
    text: str
    granted: bool


class ConsentCatalogueItemOut(BaseModel):
    kind: str
    text: str
    required_for_registration: bool


class ConsentCatalogueOut(BaseModel):
    policy_version: str
    consents: list[ConsentCatalogueItemOut]


class ConsentListOut(BaseModel):
    policy_version: str
    consents: list[ConsentStateOut]


class ConsentAcceptedOut(BaseModel):
    policy_version: str
    kind: str
    already_granted: bool


class MeOut(BaseModel):
    account: AccountOut
    expires_at: str
    consents: list[ConsentStateOut]


class HealthOut(BaseModel):
    """Deliberately narrow: no paths, no exception text, no settings dump."""

    ok: bool
    schema_version: int
    expected_schema_version: int
    checks: dict[str, bool]


class ErrorDetail(BaseModel):
    code: str
    message: str


class ErrorOut(BaseModel):
    error: ErrorDetail


# --------------------------------------------------------------------------- #
# profile
# --------------------------------------------------------------------------- #


class ProfileRequest(_StrictModel):
    """The profile fields a student may edit.

    ``user_id``, ``consent_given_at`` and the timestamps are deliberately absent:
    the server derives them from the session and the consent ledger, so a payload
    cannot claim a consent or write to another student's row.
    """

    full_name: str
    contact_email: str
    preferred_name: str | None = None
    phone: str | None = None
    institution: str | None = None
    department: str | None = None
    country: str | None = None
    degree_type: str | None = None
    current_year: int | None = None
    expected_graduation: str | None = None
    gpa: float | None = Field(default=None, allow_inf_nan=False)
    gpa_scale: float | None = Field(default=None, allow_inf_nan=False)
    coursework: list[str] = Field(default_factory=list)
    skills: list[str] = Field(default_factory=list)
    research_keywords: list[str] = Field(default_factory=list)
    target_countries: list[str] = Field(default_factory=list)
    internship_start: str | None = None
    internship_end: str | None = None
    funding_required: bool | None = None


class ProfileOut(_StrictModel):
    user_id: int
    full_name: str
    contact_email: str
    preferred_name: str | None
    phone: str | None
    institution: str | None
    department: str | None
    country: str | None
    degree_type: str | None
    current_year: int | None
    expected_graduation: str | None
    gpa: float | None
    gpa_scale: float | None
    coursework: list[str]
    skills: list[str]
    research_keywords: list[str]
    target_countries: list[str]
    internship_start: str | None
    internship_end: str | None
    funding_required: bool | None
    consent_given_at: str | None
    created_at: str | None
    updated_at: str | None
    completeness_percent: int
    completeness_missing: list[str]


# --------------------------------------------------------------------------- #
# documents
# --------------------------------------------------------------------------- #


class DocumentOut(BaseModel):
    """One stored document. Never carries ``storage_path`` or the file bytes."""

    id: int
    kind: str
    display_name: str
    media_type: str
    size_bytes: int
    status: str
    sha256: str
    uploaded_at: str
    parsed_at: str | None
    parse_error: str | None


class DocumentListOut(BaseModel):
    documents: list[DocumentOut]


class ParseOut(BaseModel):
    document: DocumentOut
    character_count: int
    fact_count: int


# --------------------------------------------------------------------------- #
# facts
# --------------------------------------------------------------------------- #


class FactOut(BaseModel):
    id: int
    field: str
    value: str
    source: str
    source_name: str | None = None
    status: str
    confidence: float | None


class FactGroupOut(BaseModel):
    field: str
    pending: list[FactOut]
    confirmed: list[FactOut]
    has_conflict: bool


class FactListOut(BaseModel):
    groups: list[FactGroupOut]


class FactUpdateRequest(_StrictModel):
    """Confirm or reject one candidate.

    ``corrected_value`` replaces the extracted text instead of accepting it; it is
    only meaningful when confirming, and the service rejects a blank value.
    """

    status: Literal["confirmed", "rejected"]
    corrected_value: str | None = None


class FactUpdateOut(BaseModel):
    fact_id: int
    status: str
    already_set: bool


class FactApplyRequest(_StrictModel):
    fields: list[str] | None = None
    overwrite: bool = False


class FactApplyOut(BaseModel):
    applied: list[str]


# --------------------------------------------------------------------------- #
# research browsing (§13.2)
# --------------------------------------------------------------------------- #


class ProgramOut(BaseModel):
    """A cached program with everything the browse/detail screen needs.

    ``stale`` is per row: a stale program may be shown for orientation but is
    never presented as verified for outreach or deadline decisions.
    """

    id: int
    name: str
    institution: str | None
    institution_id: int | None
    department: str | None
    department_id: int | None
    country: str | None
    opportunity_type: str | None
    degree_level: str | None
    research_areas: list[str]
    official_url: str
    evidence_urls: list[str]
    eligibility: list[str]
    application_method: str | None
    opening_date: str | None
    closing_date: str | None
    start_date: str | None
    duration: str | None
    stipend: str | None
    tuition: str | None
    accommodation: str | None
    required_documents: list[str]
    status: str
    confidence: float | None
    last_checked_at: str | None
    stale: bool


class ProgramListOut(BaseModel):
    programs: list[ProgramOut]
    total: int
    limit: int
    offset: int


class InstitutionOut(BaseModel):
    id: int
    name: str
    country: str | None
    website_url: str | None
    program_count: int
    department_count: int
    faculty_count: int
    last_checked_at: str | None
    stale: bool


class InstitutionListOut(BaseModel):
    institutions: list[InstitutionOut]
    total: int
    limit: int
    offset: int


class DepartmentOut(BaseModel):
    id: int
    institution_id: int
    name: str
    website_url: str | None
    program_count: int
    faculty_count: int
    last_checked_at: str | None
    stale: bool


class DepartmentListOut(BaseModel):
    institution: InstitutionOut
    departments: list[DepartmentOut]
    total: int
    limit: int
    offset: int


class PublicationOut(BaseModel):
    """A paper attributed to a faculty member by a stated identifier (ADR-020)."""

    id: int
    title: str
    year: int | None
    doi: str | None
    url: str
    venue: str | None
    authors: list[str]
    recent: bool
    last_checked_at: str | None
    stale: bool


class PublicationDetailOut(PublicationOut):
    abstract: str | None


class FacultyOut(BaseModel):
    """A cached faculty profile for the browse screen.

    ``email`` is set only for a verified address (ADR-021); an unverified one is
    represented by ``email_status`` and ``email_source_url`` alone. Outreach
    must gate on ``contact_sendable``, never on the presence of ``email``: a
    stale record keeps its verified address visible but is not sendable.
    """

    id: int
    full_name: str
    title: str | None
    institution: str
    institution_id: int
    department: str | None
    department_id: int | None
    country: str | None
    lab_name: str | None
    profile_url: str
    evidence_urls: list[str]
    research_interests: list[str]
    email: str | None
    email_status: Literal["verified", "unverified", "unavailable"]
    email_source_url: str | None
    contact_sendable: bool
    orcid: str | None
    recruitment_evidence_url: str | None
    highlighted_publications: list[PublicationOut]
    publication_count: int
    confidence: float | None
    last_checked_at: str | None
    stale: bool


class FacultyDetailOut(FacultyOut):
    """One faculty member with every attributed paper, newest first."""

    publications: list[PublicationDetailOut]


class FacultyListOut(BaseModel):
    faculty: list[FacultyOut]
    total: int
    limit: int
    offset: int


# --------------------------------------------------------------------------- #
# matching and shortlist (Phase 6, ADR-022)
# --------------------------------------------------------------------------- #


class GateOut(BaseModel):
    """A hard eligibility condition. ``unknown`` means the source did not say."""

    key: str
    label: str
    status: Literal["pass", "fail", "unknown"]
    reason: str


class FactorOut(BaseModel):
    key: str
    label: str
    weight: float
    credit: float
    points: float
    status: Literal["met", "partial", "unmet", "unknown", "not_applicable"]
    reason: str
    matched: list[str]


class MatchOut(BaseModel):
    """An explained 0-100 score. An ``ineligible`` result is always ranked last."""

    score: int
    eligibility: Literal["eligible", "ineligible", "unknown", "not_assessed"]
    gates: list[GateOut]
    factors: list[FactorOut]
    gaps: list[str]
    scoring_version: str
    evaluated_on: str


class FacultyMatchOut(BaseModel):
    faculty: FacultyOut
    match: MatchOut
    shortlist_status: Literal["shortlisted", "excluded"] | None


class FacultyMatchListOut(BaseModel):
    items: list[FacultyMatchOut]
    total: int
    limit: int
    offset: int
    #: True when more records matched than one request scores.
    truncated: bool


class ProgramMatchOut(BaseModel):
    program: ProgramOut
    match: MatchOut
    shortlist_status: Literal["shortlisted", "excluded"] | None


class ProgramMatchListOut(BaseModel):
    items: list[ProgramMatchOut]
    total: int
    limit: int
    offset: int
    truncated: bool


class ShortlistTarget(_StrictModel):
    type: Literal["faculty", "program"]
    id: int = Field(ge=1)


class ShortlistChangeRequest(_StrictModel):
    """A multi-select change; at most 50 targets per request."""

    items: list[ShortlistTarget] = Field(min_length=1, max_length=50)


class ShortlistChangeOut(BaseModel):
    changed: list[ShortlistTarget]
    unchanged: list[ShortlistTarget]


class SnapshotOut(BaseModel):
    """The explained score stored when the target was shortlisted or refreshed."""

    score: int
    eligibility: Literal["eligible", "ineligible", "unknown", "not_assessed"]
    scoring_version: str
    evaluated_on: str
    gates: list[GateOut]
    factors: list[FactorOut]
    gaps: list[str]
    created_at: str


class ShortlistEntryOut(BaseModel):
    id: int
    target_type: Literal["faculty", "program"]
    target_id: int
    #: ``None`` when the cached record no longer exists.
    target_name: str | None
    status: Literal["shortlisted", "excluded"]
    snapshot: SnapshotOut | None
    #: False when the profile or record changed since the snapshot; refresh to
    #: store a new one. ``None`` when there is nothing to compare.
    current: bool | None
    created_at: str
    updated_at: str


class ShortlistListOut(BaseModel):
    items: list[ShortlistEntryOut]


# --------------------------------------------------------------------------- #
# CV versions and suggestions (Phase 7, ADR-024)
# --------------------------------------------------------------------------- #


class CvSectionOut(BaseModel):
    kind: str
    heading: str | None
    bullets: list[str]


class CvVersionOut(BaseModel):
    """A CV version. ``original`` is immutable; ``tailored`` exists only after approval."""

    id: int
    document_id: int
    kind: Literal["original", "tailored"]
    parent_version_id: int | None
    target_type: Literal["faculty", "program"] | None
    target_id: int | None
    #: The professor's or program's name for a tailored version (QA n1 on #48).
    target_name: str | None = None
    sections: list[CvSectionOut]
    created_at: str
    approved_at: str | None


class CvVersionListOut(BaseModel):
    versions: list[CvVersionOut]


class CvOriginalRequest(_StrictModel):
    document_id: int = Field(ge=1)


class CvAnalysisRequest(_StrictModel):
    version_id: int = Field(ge=1)
    target_type: Literal["faculty", "program"]
    target_id: int = Field(ge=1)


class CvSuggestionOut(BaseModel):
    """A pending or decided suggestion. ``after`` reuses only the student's content."""

    id: int
    kind: Literal["reorder_sections", "reorder_bullets", "surface_skill", "flag_vague"]
    section: int | None
    bullet: int | None
    before: list[str]
    after: list[str]
    reason: str
    evidence: list[str]
    status: Literal["pending", "accepted", "rejected", "edited"]
    edited_text: str | None


class CvAnalysisOut(BaseModel):
    id: int
    version_id: int
    target_type: Literal["faculty", "program"]
    target_id: int
    target_name: str
    rules_version: str
    created_at: str
    suggestions: list[CvSuggestionOut]


class CvDecisionRequest(_StrictModel):
    status: Literal["pending", "accepted", "rejected", "edited"]
    edited_text: str | None = Field(default=None, max_length=500)


class CvDiffLineOut(BaseModel):
    op: Literal["same", "added", "removed"]
    line: str


class CvPreviewOut(BaseModel):
    """What approving now would produce, as sections and a line diff vs the parent."""

    sections: list[CvSectionOut]
    diff: list[CvDiffLineOut]
    applied: int


class CvApprovalOut(BaseModel):
    version: CvVersionOut
    diff: list[CvDiffLineOut]
