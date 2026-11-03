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
