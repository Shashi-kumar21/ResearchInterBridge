"""HTTP routes. Every rule they apply lives in the service modules."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, File, Form, Query, Request, Response, UploadFile, status

from .. import auth, consent, discovery, documents, facts, parsing, profiles, ratelimit
from ..errors import (
    AuthError,
    DocumentNotFoundError,
    DocumentTooLargeError,
    FactNotFoundError,
)
from ..health import check_health
from .deps import (
    ClientAddress,
    Connection,
    CurrentAccount,
    CurrentSession,
    SettingsDep,
    clear_session_cookie,
    consent_states,
    set_session_cookie,
)
from .schemas import (
    AccountOut,
    ConsentAcceptedOut,
    ConsentCatalogueItemOut,
    ConsentCatalogueOut,
    ConsentListOut,
    ConsentRequest,
    DeleteAccountRequest,
    DepartmentListOut,
    DepartmentOut,
    DocumentListOut,
    DocumentOut,
    FactApplyOut,
    FactApplyRequest,
    FactGroupOut,
    FactListOut,
    FactOut,
    FactUpdateOut,
    FactUpdateRequest,
    HealthOut,
    InstitutionListOut,
    InstitutionOut,
    LoginRequest,
    MeOut,
    ParseOut,
    ProfileOut,
    ProfileRequest,
    ProgramListOut,
    ProgramOut,
    RegisterRequest,
    SessionOut,
)

router = APIRouter(prefix="/api")

#: Stable error codes -> HTTP status. Kept next to the routes so a new service
#: error cannot be added without deciding what it means over HTTP.
AUTH_STATUS_CODES: dict[str, int] = {
    "invalid_input": status.HTTP_400_BAD_REQUEST,
    "invalid_credentials": status.HTTP_401_UNAUTHORIZED,
    "invalid_session": status.HTTP_401_UNAUTHORIZED,
    "account_locked": status.HTTP_429_TOO_MANY_REQUESTS,
    "wrong_password": status.HTTP_403_FORBIDDEN,
    "email_taken": status.HTTP_409_CONFLICT,
    "account_missing": status.HTTP_404_NOT_FOUND,
    "internal_error": status.HTTP_500_INTERNAL_SERVER_ERROR,
}

CONSENT_STATUS_CODES: dict[str, int] = {
    "unknown_consent": status.HTTP_400_BAD_REQUEST,
    "consent_required": status.HTTP_403_FORBIDDEN,
}

PROFILE_STATUS_CODES: dict[str, int] = {
    "invalid_profile": status.HTTP_400_BAD_REQUEST,
    "profile_missing": status.HTTP_404_NOT_FOUND,
    "fact_missing": status.HTTP_404_NOT_FOUND,
}

DOCUMENT_STATUS_CODES: dict[str, int] = {
    "invalid_document": status.HTTP_400_BAD_REQUEST,
    "document_missing": status.HTTP_404_NOT_FOUND,
    "document_too_large": status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
    "unsupported_document": status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
    "document_unreadable": status.HTTP_422_UNPROCESSABLE_ENTITY,
    "deletion_incomplete": status.HTTP_409_CONFLICT,
}

DISCOVERY_STATUS_CODES: dict[str, int] = {
    "invalid_discovery": status.HTTP_400_BAD_REQUEST,
    "program_missing": status.HTTP_404_NOT_FOUND,
    "institution_missing": status.HTTP_404_NOT_FOUND,
    # No adapter is configured for live discovery; the browse routes never
    # raise this, but a future search route must not turn it into a 500.
    "discovery_unavailable": status.HTTP_503_SERVICE_UNAVAILABLE,
}

#: Bytes read per call when draining an upload. The whole file is never trusted
#: to fit: Starlette spools it to disk, and this cap stops a hostile upload from
#: being pulled into memory before the service's own size check can run.
_UPLOAD_CHUNK_BYTES = 1 << 20


def _account_out(account: auth.Account) -> AccountOut:
    return AccountOut(
        user_id=account.user_id,
        email=account.email,
        created_at=account.created_at,
    )


def _iso(value: object) -> str | None:
    """Render a date for JSON; profiles keep dates, not datetimes."""

    if value is None:
        return None
    isoformat = getattr(value, "isoformat", None)
    return str(isoformat()) if callable(isoformat) else str(value)


def _profile_out(profile: profiles.StudentProfile) -> ProfileOut:
    report = profiles.completeness(profile)
    return ProfileOut(
        user_id=profile.user_id,
        full_name=profile.full_name,
        contact_email=profile.contact_email,
        preferred_name=profile.preferred_name,
        phone=profile.phone,
        institution=profile.institution,
        department=profile.department,
        country=profile.country,
        degree_type=None if profile.degree_type is None else profile.degree_type.value,
        current_year=profile.current_year,
        expected_graduation=_iso(profile.expected_graduation),
        gpa=profile.gpa,
        gpa_scale=profile.gpa_scale,
        coursework=list(profile.coursework),
        skills=list(profile.skills),
        research_keywords=list(profile.research_keywords),
        target_countries=list(profile.target_countries),
        internship_start=_iso(profile.internship_start),
        internship_end=_iso(profile.internship_end),
        funding_required=profile.funding_required,
        consent_given_at=profile.consent_given_at,
        created_at=profile.created_at,
        updated_at=profile.updated_at,
        completeness_percent=report.percent,
        completeness_missing=list(report.missing),
    )


def _document_out(record: documents.DocumentRecord) -> DocumentOut:
    return DocumentOut(
        id=record.id,
        kind=record.kind,
        display_name=record.display_name,
        media_type=record.media_type,
        size_bytes=record.size_bytes,
        status=record.status,
        sha256=record.sha256,
        uploaded_at=record.uploaded_at,
        parsed_at=record.parsed_at,
        parse_error=record.parse_error,
    )


def _fact_out(candidate: facts.FactCandidate) -> FactOut:
    return FactOut(
        id=candidate.id,
        field=candidate.field_name,
        value=candidate.value,
        source=candidate.source,
        source_name=candidate.source_name,
        status=candidate.status,
        confidence=candidate.confidence,
    )


def _group_out(group: facts.FactGroup) -> FactGroupOut:
    return FactGroupOut(
        field=group.field_name,
        pending=[_fact_out(candidate) for candidate in group.pending],
        confirmed=[_fact_out(candidate) for candidate in group.confirmed],
        has_conflict=group.has_conflict,
    )


def _program_out(program: discovery.Program) -> ProgramOut:
    return ProgramOut(
        id=program.id,
        name=program.name,
        institution=program.institution,
        institution_id=program.institution_id,
        department=program.department,
        department_id=program.department_id,
        country=program.country,
        opportunity_type=program.opportunity_type,
        degree_level=program.degree_level,
        research_areas=list(program.research_areas),
        official_url=program.official_url,
        evidence_urls=list(program.evidence_urls),
        eligibility=list(program.eligibility),
        application_method=program.application_method,
        opening_date=program.opening_date,
        closing_date=program.closing_date,
        start_date=program.start_date,
        duration=program.duration,
        stipend=program.stipend,
        tuition=program.tuition,
        accommodation=program.accommodation,
        required_documents=list(program.required_documents),
        status=program.status,
        confidence=program.confidence,
        last_checked_at=program.last_checked_at,
        stale=program.stale,
    )


def _institution_out(institution: discovery.Institution) -> InstitutionOut:
    return InstitutionOut(
        id=institution.id,
        name=institution.name,
        country=institution.country,
        website_url=institution.website_url,
        program_count=institution.program_count,
        department_count=institution.department_count,
        last_checked_at=institution.last_checked_at,
        stale=institution.stale,
    )


def _department_out(department: discovery.Department) -> DepartmentOut:
    return DepartmentOut(
        id=department.id,
        institution_id=department.institution_id,
        name=department.name,
        website_url=department.website_url,
        program_count=department.program_count,
        last_checked_at=department.last_checked_at,
        stale=department.stale,
    )


def _read_upload(upload: UploadFile) -> bytes:
    """Read at most :data:`documents.MAX_DOCUMENT_BYTES` from an upload.

    Reading in chunks means an oversized upload is refused before the whole body
    is held in memory, even though Starlette has already spooled it to disk.
    """

    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = upload.file.read(_UPLOAD_CHUNK_BYTES)
        if not chunk:
            break
        total += len(chunk)
        if total > documents.MAX_DOCUMENT_BYTES:
            raise DocumentTooLargeError(
                f"{upload.filename or 'the upload'} is larger than the "
                f"{documents.MAX_DOCUMENT_BYTES} byte limit."
            )
        chunks.append(chunk)
    return b"".join(chunks)


@router.get("/health", response_model=HealthOut, tags=["system"])
def health(settings: SettingsDep, response: Response) -> HealthOut:
    """Read-only readiness probe.

    Unauthenticated by design (a monitor has no account) and deliberately narrow:
    it reports schema state and which checks passed, never paths or exception
    text. The CLI ``health`` command prints the full report for an operator.
    """

    report = check_health(settings)
    if not report.ok:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    return HealthOut(
        ok=report.ok,
        schema_version=report.schema_version,
        expected_schema_version=report.expected_schema_version,
        checks=dict(report.checks),
    )


@router.get("/consents/catalogue", response_model=ConsentCatalogueOut, tags=["consent"])
def consent_catalogue() -> ConsentCatalogueOut:
    """Public consent texts, so the registration screen renders our wording.

    No user data and no session are involved: this is the same text the CLI
    prints and the database records against a policy version.
    """

    return ConsentCatalogueOut(
        policy_version=consent.POLICY_VERSION,
        consents=[
            ConsentCatalogueItemOut(
                kind=kind,
                text=text,
                required_for_registration=kind in consent.REQUIRED_CONSENTS,
            )
            for kind, text in sorted(consent.CONSENT_TEXTS.items())
        ],
    )


@router.post(
    "/auth/register",
    response_model=SessionOut,
    status_code=status.HTTP_201_CREATED,
    tags=["auth"],
)
def register(
    payload: RegisterRequest,
    response: Response,
    connection: Connection,
    settings: SettingsDep,
    client: ClientAddress,
) -> SessionOut:
    """Create an account and sign it in.

    Consent is required *before* the account exists, so the service refuses an
    incomplete registration rather than creating a half-consented account.
    The rate check runs first: an automated flood must not cost one scrypt hash
    per request.

    A duplicate email answers ``409 email_taken``, so registration does reveal
    whether an address already has an account. That is unavoidable for an
    endpoint whose job is to reject duplicates; login is the route that tries to
    stay non-enumerable.
    """

    ratelimit.check(connection, rule=ratelimit.REGISTER_RULE, client=client)
    account = auth.register(
        connection,
        email=payload.email,
        password=payload.password,
        consents=set(payload.consents),
    )
    session = auth.create_session(connection, user_id=account.user_id)
    if session.token is None:  # pragma: no cover - create_session always returns one
        raise AuthError("session token was not created.", code="internal_error")
    set_session_cookie(response, settings, session.token)
    # The body carries an account and the response sets a cookie: never cache it.
    response.headers["Cache-Control"] = "no-store"
    return SessionOut(account=_account_out(account), expires_at=session.expires_at)


@router.post("/auth/login", response_model=SessionOut, tags=["auth"])
def login(
    payload: LoginRequest,
    response: Response,
    connection: Connection,
    settings: SettingsDep,
    client: ClientAddress,
) -> SessionOut:
    """Verify credentials and set the session cookie.

    Unknown email and wrong password return one identical body and status, so a
    probe cannot tell registered addresses from unknown ones. Three residual
    signals remain on purpose, and are documented rather than hidden:

    * a locked account answers ``429 account_locked``, but only after five wrong
      guesses and only for an address that exists;
    * this route's own rate limit answers ``429 rate_limited`` uniformly, before
      the account is read;
    * response timing is equalised by hashing a placeholder for unknown emails.

    ``Cache-Control: no-store`` keeps a shared cache from storing a session
    response.
    """

    ratelimit.check(connection, rule=ratelimit.LOGIN_RULE, client=client)
    session = auth.authenticate(connection, email=payload.email, password=payload.password)
    account = auth.get_account(connection, session.user_id)
    if session.token is None:  # pragma: no cover - authenticate always returns one
        raise AuthError("session token was not created.", code="internal_error")
    set_session_cookie(response, settings, session.token)
    response.headers["Cache-Control"] = "no-store"
    return SessionOut(account=_account_out(account), expires_at=session.expires_at)


@router.post(
    "/auth/logout",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    tags=["auth"],
)
def logout(
    request: Request,
    response: Response,
    connection: Connection,
    settings: SettingsDep,
) -> None:
    """Revoke the current session and clear the cookie.

    Idempotent: a missing, expired or already-revoked cookie is not an error, and
    the cookie is cleared in every case so a stale token cannot linger.
    """

    token = request.cookies.get(settings.session_cookie_name)
    if token:
        auth.revoke_session(connection, token)
    clear_session_cookie(response, settings)


@router.get("/auth/me", response_model=MeOut, tags=["auth"])
def me(
    session: CurrentSession,
    account: CurrentAccount,
    connection: Connection,
    response: Response,
) -> MeOut:
    """Describe the signed-in account and its consent state."""

    response.headers["Cache-Control"] = "no-store"
    return MeOut(
        account=_account_out(account),
        expires_at=session.expires_at,
        consents=consent_states(connection, account.user_id),
    )


@router.get("/auth/export", response_model=dict[str, Any], tags=["auth"])
def export_data(
    account: CurrentAccount,
    connection: Connection,
    response: Response,
) -> dict[str, Any]:
    """Everything this instance holds for the signed-in account.

    Excludes password hashes, session tokens and document file contents: the
    export is the same structure the CLI and the deletion guarantee describe.
    """

    response.headers["Cache-Control"] = "no-store"
    return auth.export_account_data(connection, account.user_id)


@router.delete(
    "/auth/account",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    tags=["auth"],
)
def delete_account(
    payload: DeleteAccountRequest,
    account: CurrentAccount,
    connection: Connection,
    settings: SettingsDep,
    response: Response,
) -> None:
    """Delete the account and its data, after re-checking the password.

    The password is required again because a stolen session must not be enough
    to destroy a student's work. Deletion removes profile rows, stored documents
    (files included), facts, consents and every session, then clears the cookie.
    """

    auth.delete_account(
        connection,
        user_id=account.user_id,
        password=payload.password,
        data_directory=settings.data_directory,
    )
    clear_session_cookie(response, settings)


@router.get("/consents", response_model=ConsentListOut, tags=["consent"])
def list_consents(
    account: CurrentAccount, connection: Connection, response: Response
) -> ConsentListOut:
    """Consent state for the signed-in account."""

    response.headers["Cache-Control"] = "no-store"
    return ConsentListOut(
        policy_version=consent.POLICY_VERSION,
        consents=consent_states(connection, account.user_id),
    )


@router.post("/consents", response_model=ConsentAcceptedOut, tags=["consent"])
def accept_consent(
    payload: ConsentRequest,
    account: CurrentAccount,
    connection: Connection,
) -> ConsentAcceptedOut:
    """Record one consent for the signed-in account.

    Recording is idempotent, so a retried request reports
    ``already_granted: true`` instead of failing or writing a duplicate row. The
    service writes the single audit row, so this route does not add another.
    """

    created = consent.record_consent(
        connection,
        user_id=account.user_id,
        kind=payload.kind,
        actor=f"user:{account.user_id}",
    )
    return ConsentAcceptedOut(
        policy_version=consent.POLICY_VERSION,
        kind=payload.kind,
        already_granted=not created,
    )


# --------------------------------------------------------------------------- #
# profile
# --------------------------------------------------------------------------- #


@router.get("/profile", response_model=ProfileOut, tags=["profile"])
def get_profile(account: CurrentAccount, connection: Connection, response: Response) -> ProfileOut:
    """Return the signed-in student's profile and its completeness.

    A student who has not saved one yet gets ``404 profile_missing``: an empty
    profile is not invented here, because the onboarding screen must distinguish
    "not started" from "saved with blank fields".
    """

    response.headers["Cache-Control"] = "no-store"
    return _profile_out(profiles.load_profile(connection, account.user_id))


@router.put("/profile", response_model=ProfileOut, tags=["profile"])
def put_profile(
    payload: ProfileRequest,
    account: CurrentAccount,
    connection: Connection,
    response: Response,
) -> ProfileOut:
    """Create or replace the signed-in student's profile.

    ``PUT`` is a full replacement, so an omitted optional field is cleared. The
    server fills ``user_id`` from the session and ``consent_given_at`` from the
    consent ledger; neither is accepted from the request body.
    """

    response.headers["Cache-Control"] = "no-store"
    profile = profiles.StudentProfile(
        user_id=account.user_id,
        full_name=payload.full_name,
        contact_email=payload.contact_email,
        preferred_name=payload.preferred_name,
        phone=payload.phone,
        institution=payload.institution,
        department=payload.department,
        country=payload.country,
        degree_type=payload.degree_type,
        current_year=payload.current_year,
        expected_graduation=payload.expected_graduation,
        gpa=payload.gpa,
        gpa_scale=payload.gpa_scale,
        coursework=tuple(payload.coursework),
        skills=tuple(payload.skills),
        research_keywords=tuple(payload.research_keywords),
        target_countries=tuple(payload.target_countries),
        internship_start=payload.internship_start,
        internship_end=payload.internship_end,
        funding_required=payload.funding_required,
        consent_given_at=consent.granted_at(connection, account.user_id, "document_processing"),
    )
    stored = profiles.save_profile(connection, profile, actor=f"user:{account.user_id}")
    return _profile_out(stored)


# --------------------------------------------------------------------------- #
# documents
# --------------------------------------------------------------------------- #


@router.get("/documents", response_model=DocumentListOut, tags=["documents"])
def list_documents(
    account: CurrentAccount, connection: Connection, response: Response
) -> DocumentListOut:
    """List the signed-in student's documents, newest first."""

    response.headers["Cache-Control"] = "no-store"
    records = documents.list_documents(connection, account.user_id)
    return DocumentListOut(documents=[_document_out(record) for record in records])


@router.post(
    "/documents",
    response_model=DocumentOut,
    status_code=status.HTTP_201_CREATED,
    tags=["documents"],
)
def upload_document(
    file: Annotated[UploadFile, File()],
    account: CurrentAccount,
    connection: Connection,
    settings: SettingsDep,
    response: Response,
    kind: Annotated[str, Form()] = "cv",
) -> DocumentOut:
    """Validate, store and record one CV/transcript/other document.

    The service enforces the ``document_processing`` consent, the extension and
    content checks, the size limit and re-upload de-duplication. This route only
    drains the upload with a hard cap and maps the result.
    """

    response.headers["Cache-Control"] = "no-store"
    payload = _read_upload(file)
    record = documents.save_document(
        connection,
        user_id=account.user_id,
        display_name=file.filename or "",
        payload=payload,
        data_directory=settings.data_directory,
        kind=kind,
        declared_media_type=file.content_type,
        actor=f"user:{account.user_id}",
    )
    return _document_out(record)


@router.get("/documents/{document_id}", response_model=DocumentOut, tags=["documents"])
def get_document(
    document_id: int,
    account: CurrentAccount,
    connection: Connection,
    response: Response,
) -> DocumentOut:
    """Return one document owned by the signed-in student."""

    response.headers["Cache-Control"] = "no-store"
    return _document_out(
        documents.get_document(connection, document_id=document_id, user_id=account.user_id)
    )


@router.post("/documents/{document_id}/parse", response_model=ParseOut, tags=["documents"])
def parse_document(
    document_id: int,
    account: CurrentAccount,
    connection: Connection,
    settings: SettingsDep,
    response: Response,
) -> ParseOut:
    """Extract text and candidate facts, leaving every fact pending confirmation.

    Re-parsing is idempotent: stored facts are de-duplicated by
    ``(user, field, value, source)``, so a retry neither duplicates a candidate
    nor confirms one.
    """

    response.headers["Cache-Control"] = "no-store"
    result = parsing.parse_document(
        connection,
        document_id=document_id,
        user_id=account.user_id,
        data_directory=settings.data_directory,
        actor=f"user:{account.user_id}",
    )
    return ParseOut(
        document=_document_out(result.document),
        character_count=result.character_count,
        fact_count=len(result.fact_ids),
    )


@router.delete(
    "/documents/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    response_model=None,
    tags=["documents"],
)
def delete_document(
    document_id: int,
    account: CurrentAccount,
    connection: Connection,
    settings: SettingsDep,
) -> None:
    """Delete one document and its stored file.

    Extracted facts are kept: a fact the student confirmed is profile data in its
    own right, not a copy of the file.
    """

    deleted = documents.delete_document(
        connection,
        document_id=document_id,
        user_id=account.user_id,
        data_directory=settings.data_directory,
        actor=f"user:{account.user_id}",
    )
    if not deleted:
        raise DocumentNotFoundError(f"document {document_id} does not exist for this account.")


# --------------------------------------------------------------------------- #
# facts
# --------------------------------------------------------------------------- #


@router.get("/facts", response_model=FactListOut, tags=["facts"])
def list_facts(account: CurrentAccount, connection: Connection, response: Response) -> FactListOut:
    """Group the student's fact candidates by field, conflicts included."""

    response.headers["Cache-Control"] = "no-store"
    groups = facts.fact_groups(connection, account.user_id)
    return FactListOut(groups=[_group_out(group) for group in groups])


@router.post("/facts/apply", response_model=FactApplyOut, tags=["facts"])
def apply_facts(
    payload: FactApplyRequest,
    account: CurrentAccount,
    connection: Connection,
    response: Response,
) -> FactApplyOut:
    """Copy confirmed facts into the profile.

    Only bound fields are applicable, and a value the student typed is kept
    unless ``overwrite`` is explicitly true, so applying facts cannot silently
    replace their work.
    """

    response.headers["Cache-Control"] = "no-store"
    applied = facts.apply_confirmed_facts_to_profile(
        connection,
        user_id=account.user_id,
        actor=f"user:{account.user_id}",
        fields=None if payload.fields is None else frozenset(payload.fields),
        overwrite=payload.overwrite,
    )
    return FactApplyOut(applied=list(applied))


@router.patch("/facts/{fact_id}", response_model=FactUpdateOut, tags=["facts"])
def update_fact(
    fact_id: int,
    payload: FactUpdateRequest,
    account: CurrentAccount,
    connection: Connection,
    response: Response,
) -> FactUpdateOut:
    """Confirm (optionally with a correction) or reject one fact candidate."""

    response.headers["Cache-Control"] = "no-store"
    current = facts.fact_status(connection, user_id=account.user_id, fact_id=fact_id)
    if current is None:
        raise FactNotFoundError(f"fact {fact_id} does not exist for this account.")
    already_set = current == payload.status

    found = facts.update_fact(
        connection,
        user_id=account.user_id,
        fact_id=fact_id,
        actor=f"user:{account.user_id}",
        status=payload.status,
        corrected_value=payload.corrected_value,
    )
    if not found:  # pragma: no cover - the row was just read in this transaction
        raise FactNotFoundError(f"fact {fact_id} does not exist for this account.")

    return FactUpdateOut(fact_id=fact_id, status=payload.status, already_set=already_set)


# --------------------------------------------------------------------------- #
# research browsing (§13.2)
# --------------------------------------------------------------------------- #


@router.get("/research/programs", response_model=ProgramListOut, tags=["research"])
def list_research_programs(
    account: CurrentAccount,
    connection: Connection,
    response: Response,
    area: Annotated[str | None, Query(max_length=200)] = None,
    country: Annotated[str | None, Query(max_length=200)] = None,
    department: Annotated[str | None, Query(max_length=200)] = None,
    degree_level: Annotated[str | None, Query(max_length=200)] = None,
    institution_id: Annotated[int | None, Query(ge=1)] = None,
    department_id: Annotated[int | None, Query(ge=1)] = None,
    limit: Annotated[int, Query(ge=1, le=discovery.MAX_BROWSE_LIMIT)] = (
        discovery.DEFAULT_BROWSE_LIMIT
    ),
    offset: Annotated[int, Query(ge=0)] = 0,
) -> ProgramListOut:
    """Browse the shared program cache; this never calls a source.

    Cache rows are public research data (§13.4), but the endpoint still requires
    a session so the browse screens are not an anonymous quota-free proxy.
    A row with ``stale: true`` is orientation only, not verified for outreach.
    """

    response.headers["Cache-Control"] = "no-store"
    page = discovery.browse_programs(
        connection,
        discovery.ProgramFilters(
            research_area=area,
            country=country,
            department=department,
            degree_level=degree_level,
            institution_id=institution_id,
            department_id=department_id,
        ),
        limit=limit,
        offset=offset,
    )
    return ProgramListOut(
        programs=[_program_out(program) for program in page.programs],
        total=page.total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get("/research/programs/{program_id}", response_model=ProgramOut, tags=["research"])
def get_research_program(
    program_id: int,
    account: CurrentAccount,
    connection: Connection,
    response: Response,
) -> ProgramOut:
    """Return one cached program; an unknown id is ``404 program_missing``."""

    response.headers["Cache-Control"] = "no-store"
    return _program_out(discovery.get_program(connection, program_id))


@router.get("/research/institutions", response_model=InstitutionListOut, tags=["research"])
def list_research_institutions(
    account: CurrentAccount,
    connection: Connection,
    response: Response,
    country: Annotated[str | None, Query(max_length=200)] = None,
    limit: Annotated[int, Query(ge=1, le=discovery.MAX_BROWSE_LIMIT)] = (
        discovery.DEFAULT_BROWSE_LIMIT
    ),
    offset: Annotated[int, Query(ge=0)] = 0,
) -> InstitutionListOut:
    """Browse cached institutions with their program/department counts."""

    response.headers["Cache-Control"] = "no-store"
    page = discovery.browse_institutions(connection, country=country, limit=limit, offset=offset)
    return InstitutionListOut(
        institutions=[_institution_out(institution) for institution in page.institutions],
        total=page.total,
        limit=page.limit,
        offset=page.offset,
    )


@router.get(
    "/research/institutions/{institution_id}/departments",
    response_model=DepartmentListOut,
    tags=["research"],
)
def list_research_departments(
    institution_id: int,
    account: CurrentAccount,
    connection: Connection,
    response: Response,
    limit: Annotated[int, Query(ge=1, le=discovery.MAX_BROWSE_LIMIT)] = (
        discovery.DEFAULT_BROWSE_LIMIT
    ),
    offset: Annotated[int, Query(ge=0)] = 0,
) -> DepartmentListOut:
    """Browse one institution's cached departments.

    An unknown institution is ``404 institution_missing``; a known institution
    with no cached departments is an empty page, which the screen can explain.
    """

    response.headers["Cache-Control"] = "no-store"
    institution = discovery.get_institution(connection, institution_id)
    page = discovery.browse_departments(connection, institution_id, limit=limit, offset=offset)
    return DepartmentListOut(
        institution=_institution_out(institution),
        departments=[_department_out(department) for department in page.departments],
        total=page.total,
        limit=page.limit,
        offset=page.offset,
    )
