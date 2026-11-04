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

    status: str
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
