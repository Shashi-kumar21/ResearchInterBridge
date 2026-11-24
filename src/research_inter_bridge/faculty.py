"""Faculty and publication discovery over the shared research cache.

Phase 5 of ``task.md`` (§3.5, §13). This module mirrors :mod:`discovery` for
professors and their papers, and keeps the same cache-first contract:

* An adapter turns one official source into :class:`DiscoveredFaculty` records,
  each with the publications that source attributes to that person. It must not
  invent an email, a paper, an author or a date.
* Identity is the official profile URL, never the name. Two people with the same
  name at one university are two rows; nothing here merges records by name.
* An email is stored only together with the page that lists it. It is
  ``verified`` only when an ``official`` adapter (operator-configured
  institutional pages) found it on the profile's host; every other listing is
  ``unverified`` and never sendable. Nothing in this module derives an address
  from a naming pattern.
* Papers are linked to a person only because the source attributed them to that
  profile. Author names are display text and are never used to link a person.
* A fresh query performs zero adapter calls; a failed refresh keeps the previous
  rows, marks them stale and never deletes or guesses data. ``offline=True``
  never calls an adapter (§13.2.5).
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any, Protocol
from urllib.parse import urlsplit, urlunsplit

from . import db, discovery
from . import research_cache as cache
from .errors import CacheError, DiscoveryError, DiscoveryUnavailableError, FacultyNotFoundError

#: Contact states shown to callers. ``unavailable`` means no page listed one.
EMAIL_STATUSES: frozenset[str] = frozenset({"verified", "unverified", "unavailable"})

#: How far an adapter's pages are trusted for contact details (ADR-018). Only an
#: ``official`` adapter (operator-configured institutional pages) can produce a
#: ``verified`` address. Everything else — ORCID, scholarly indexes, aggregators,
#: personal sites — is ``none``, and its addresses stay ``unverified`` on any host.
CONTACT_AUTHORITIES: frozenset[str] = frozenset({"official", "none"})

MAX_PUBLICATIONS = 50
MAX_AUTHORS = 500
MAX_ABSTRACT_LENGTH = 10_000
MAX_EMAIL_LENGTH = 254

#: How many papers a profile highlights (§3.5: "two or three relevant recent
#: publications, prioritising the last three years where possible").
HIGHLIGHTED_PUBLICATIONS = 3
RECENT_PUBLICATION_YEARS = 3

#: The oldest year a stated publication year may carry; anything earlier is a
#: parsing error rather than a scholarly record this product needs.
MIN_PUBLICATION_YEAR = 1800

_EMAIL_RE = re.compile(
    r"^[A-Za-z0-9.!#$%&'*+/=?^_`{|}~-]+"
    r"@[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)+$"
)
_DOI_RE = re.compile(r"^10\.\d{4,9}/\S+$")
_ORCID_RE = re.compile(r"^\d{4}-\d{4}-\d{4}-\d{3}[\dX]$")
_ORCID_PREFIXES = (
    "https://orcid.org/",
    "http://orcid.org/",
    "https://www.orcid.org/",
    "http://www.orcid.org/",
    "www.orcid.org/",
    "orcid.org/",
)
_DOI_PREFIXES = ("https://doi.org/", "http://doi.org/", "https://dx.doi.org/", "http://dx.doi.org/")


@dataclass(frozen=True, slots=True)
class FacultyQuery:
    """A normalised faculty search; every field is optional."""

    research_area: str | None = None
    country: str | None = None
    institution: str | None = None
    department: str | None = None

    def normalised(self) -> FacultyQuery:
        return FacultyQuery(
            research_area=discovery._query_text(self.research_area, "research_area", 200),
            country=discovery._query_text(self.country, "country", 200),
            institution=discovery._query_text(self.institution, "institution", 300),
            department=discovery._query_text(self.department, "department", 300),
        )

    def is_empty(self) -> bool:
        return all(
            value is None
            for value in (self.research_area, self.country, self.institution, self.department)
        )

    def fingerprint(self) -> str:
        payload = self.as_dict()
        encoded = json.dumps(payload, sort_keys=True, ensure_ascii=False).encode("utf-8")
        # A distinct prefix keeps faculty and program fingerprints apart in the
        # shared query index even for identical filter values.
        return f"faculty-query:{hashlib.sha256(encoded).hexdigest()}"

    def as_dict(self) -> dict[str, str | None]:
        normalised = self.normalised()
        return {
            "research_area": normalised.research_area,
            "country": normalised.country,
            "institution": normalised.institution,
            "department": normalised.department,
        }


@dataclass(frozen=True, slots=True)
class DiscoveredPublication:
    """One paper a source attributes to a faculty profile.

    ``url`` may be omitted only when a DOI is stated; the DOI resolver then
    becomes the source link. Unstated fields stay ``None``/empty.
    """

    title: str
    url: str | None = None
    doi: str | None = None
    year: int | None = None
    authors: tuple[str, ...] = ()
    venue: str | None = None
    abstract: str | None = None


@dataclass(frozen=True, slots=True)
class DiscoveredFaculty:
    """One adapter record for a person on an official faculty or lab page."""

    full_name: str
    institution: str
    profile_url: str
    country: str | None = None
    department: str | None = None
    title: str | None = None
    lab_name: str | None = None
    research_interests: tuple[str, ...] = ()
    email: str | None = None
    email_source_url: str | None = None
    orcid: str | None = None
    recruitment_evidence_url: str | None = None
    publications: tuple[DiscoveredPublication, ...] = ()
    confidence: float | None = None
    sources: tuple[discovery.SourceRef, ...] = ()
    http_status: int | None = None
    raw_payload_path: str | None = None


@dataclass(frozen=True, slots=True)
class Publication:
    """A stored publication as returned to callers."""

    id: int
    canonical_key: str
    title: str
    year: int | None
    doi: str | None
    url: str
    venue: str | None
    abstract: str | None
    authors: tuple[str, ...]
    recent: bool
    last_checked_at: str | None
    stale: bool


@dataclass(frozen=True, slots=True)
class FacultyMember:
    """A stored faculty profile with its contact state and attributed papers."""

    id: int
    canonical_key: str
    full_name: str
    institution: str
    institution_id: int
    department: str | None
    department_id: int | None
    country: str | None
    title: str | None
    lab_name: str | None
    profile_url: str
    evidence_urls: tuple[str, ...]
    research_interests: tuple[str, ...]
    email: str | None
    email_status: str
    email_source_url: str | None
    orcid: str | None
    #: True only for a verified address on a fresh record. Outreach must check
    #: this flag, never the presence of ``email`` alone (§3.5).
    contact_sendable: bool
    recruitment_evidence_url: str | None
    #: Every attributed paper, newest first. Empty when the member was loaded
    #: as a browse summary (``include_publications=False``); use
    #: ``publication_count`` for the number, which is always exact.
    publications: tuple[Publication, ...]
    highlighted_publications: tuple[Publication, ...]
    publication_count: int
    confidence: float | None
    last_checked_at: str | None
    stale: bool


@dataclass(frozen=True, slots=True)
class FacultyResult:
    query: FacultyQuery
    faculty: tuple[FacultyMember, ...]
    served_from_cache: bool
    stale: bool
    message: str | None


@dataclass(frozen=True, slots=True)
class FacultyFilters:
    """Cache-only browse filters (§13.2). An unknown stored value never matches."""

    research_area: str | None = None
    country: str | None = None
    institution_id: int | None = None
    department_id: int | None = None

    def normalised(self) -> FacultyFilters:
        return FacultyFilters(
            research_area=discovery._query_text(self.research_area, "research_area", 200),
            country=discovery._query_text(self.country, "country", 200),
            institution_id=discovery._optional_id(self.institution_id, "institution_id"),
            department_id=discovery._optional_id(self.department_id, "department_id"),
        )


@dataclass(frozen=True, slots=True)
class FacultyPage:
    faculty: tuple[FacultyMember, ...]
    total: int
    limit: int
    offset: int


class FacultyAdapter(Protocol):
    """A faculty source. Network I/O happens only in ``search``.

    ``contact_authority`` is ``"official"`` only for an adapter that reads
    operator-configured institutional pages. An adapter without the attribute is
    treated as ``"none"``, so a new source can never verify an address by default.
    """

    name: str
    parser_version: str
    contact_authority: str

    def search(self, query: FacultyQuery) -> Sequence[DiscoveredFaculty]:
        """Return records for ``query``; raise :class:`DiscoveryError` on failure."""
        ...


# --------------------------------------------------------------------------- #
# validation
# --------------------------------------------------------------------------- #


def validate_faculty(
    record: DiscoveredFaculty, *, now: datetime | None = None
) -> DiscoveredFaculty:
    """Return a cleaned record or raise :class:`DiscoveryError`.

    The single gate between an untrusted faculty payload and the database. It
    checks shape and consistency and never fills a missing value.
    """

    if not isinstance(record, DiscoveredFaculty):
        raise DiscoveryError("a faculty adapter must return DiscoveredFaculty records.")
    checked_at = now or datetime.now(UTC)
    full_name = discovery._required_text(record.full_name, "full_name", discovery.MAX_NAME_LENGTH)
    institution = discovery._required_text(
        record.institution, "institution", discovery.MAX_NAME_LENGTH
    )
    profile_url = discovery._url(record.profile_url, "profile_url")
    email, email_source_url = _validated_email(record.email, record.email_source_url, full_name)
    confidence = _confidence(record.confidence, full_name)
    http_status = _http_status(record.http_status)

    sources = discovery._unique_sources(
        discovery.SourceRef(
            url=discovery._url(source.url, "source url"),
            publisher=discovery._optional_text(
                source.publisher, "source publisher", discovery.MAX_NAME_LENGTH
            ),
            kind=discovery._required_text(source.kind, "source kind", 50),
        )
        for source in record.sources or ()
    )
    if not any(source.url == profile_url for source in sources):
        sources = (discovery.SourceRef(url=profile_url), *sources)

    publications = _unique_publications(
        validate_publication(publication, now=checked_at)
        for publication in record.publications or ()
    )
    if len(publications) > MAX_PUBLICATIONS:
        raise DiscoveryError(
            f"faculty {full_name!r} lists {len(publications)} publications; "
            f"at most {MAX_PUBLICATIONS} are accepted per record."
        )

    recruitment = record.recruitment_evidence_url
    return replace(
        record,
        full_name=full_name,
        institution=institution,
        profile_url=profile_url,
        country=discovery._optional_text(record.country, "country", 200),
        department=discovery._optional_text(
            record.department, "department", discovery.MAX_NAME_LENGTH
        ),
        title=discovery._optional_text(record.title, "title", 200),
        lab_name=discovery._optional_text(record.lab_name, "lab_name", discovery.MAX_NAME_LENGTH),
        research_interests=discovery._text_tuple(record.research_interests, "research_interests"),
        email=email,
        email_source_url=email_source_url,
        orcid=normalise_orcid(record.orcid),
        recruitment_evidence_url=(
            None if recruitment is None else discovery._url(recruitment, "recruitment_evidence_url")
        ),
        publications=publications,
        confidence=confidence,
        sources=sources,
        http_status=http_status,
        raw_payload_path=discovery._optional_text(
            record.raw_payload_path, "raw_payload_path", discovery.MAX_URL_LENGTH
        ),
    )


def validate_publication(
    record: DiscoveredPublication, *, now: datetime | None = None
) -> DiscoveredPublication:
    """Return a cleaned publication or raise :class:`DiscoveryError`."""

    if not isinstance(record, DiscoveredPublication):
        raise DiscoveryError("a faculty record's publications must be DiscoveredPublication.")
    title = discovery._required_text(record.title, "publication title", 1000)
    doi = normalise_doi(record.doi)
    if record.url is not None:
        url = discovery._url(record.url, "publication url")
    elif doi is not None:
        url = f"https://doi.org/{doi}"
    else:
        raise DiscoveryError(
            f"publication {title!r} has neither a URL nor a DOI; every paper needs a source link."
        )
    year = record.year
    if year is not None:
        latest = (now or datetime.now(UTC)).year + 1
        if isinstance(year, bool) or not isinstance(year, int):
            raise DiscoveryError(f"publication {title!r} has a non-integer year.")
        if not MIN_PUBLICATION_YEAR <= year <= latest:
            raise DiscoveryError(
                f"publication {title!r} has year {year}; expected {MIN_PUBLICATION_YEAR}-{latest}."
            )
    authors = _authors(record.authors, title)
    abstract = record.abstract
    if abstract is not None:
        if not isinstance(abstract, str):
            raise DiscoveryError(f"publication {title!r} has a non-text abstract.")
        abstract = abstract.strip() or None
        if abstract is not None and len(abstract) > MAX_ABSTRACT_LENGTH:
            raise DiscoveryError(
                f"publication {title!r} has an abstract over {MAX_ABSTRACT_LENGTH} characters."
            )
    return replace(
        record,
        title=title,
        url=url,
        doi=doi,
        year=year,
        authors=authors,
        venue=discovery._optional_text(record.venue, "publication venue", 500),
        abstract=abstract,
    )


def normalise_doi(value: str | None) -> str | None:
    """Return a bare, lower-case DOI (``10.x/y``) or raise for a malformed one.

    DOIs are case-insensitive, so ``10.1000/ABC`` and the resolver URL form of
    the same DOI become one identity.
    """

    if value is None:
        return None
    if not isinstance(value, str):
        raise DiscoveryError("a DOI must be a string.")
    cleaned = value.strip()
    lowered = cleaned.casefold()
    for prefix in _DOI_PREFIXES:
        if lowered.startswith(prefix):
            cleaned = cleaned[len(prefix) :]
            break
    else:
        if lowered.startswith("doi:"):
            cleaned = cleaned[4:].strip()
    cleaned = cleaned.casefold()
    if not _DOI_RE.match(cleaned) or len(cleaned) > 300:
        raise DiscoveryError(f"{value!r} is not a DOI (expected 10.<registrant>/<suffix>).")
    return cleaned


def normalise_orcid(value: str | None) -> str | None:
    """Return a bare ORCID iD (``0000-0002-1825-0097``) or raise for a bad one.

    The resolver URL form is accepted. The ISO 7064 11-2 check digit is verified,
    so a mistyped iD cannot point at somebody else's record.
    """

    if value is None:
        return None
    if not isinstance(value, str):
        raise DiscoveryError("an ORCID iD must be a string.")
    cleaned = value.strip()
    lowered = cleaned.casefold()
    for prefix in _ORCID_PREFIXES:
        if lowered.startswith(prefix):
            cleaned = cleaned[len(prefix) :]
            break
    cleaned = cleaned.rstrip("/").upper()
    if not _ORCID_RE.match(cleaned) or not _orcid_checksum_ok(cleaned):
        raise DiscoveryError(f"{value!r} is not a valid ORCID iD.")
    return cleaned


def _orcid_checksum_ok(orcid: str) -> bool:
    digits = orcid.replace("-", "")
    total = 0
    for char in digits[:-1]:
        total = (total + int(char)) * 2
    check = (12 - total % 11) % 11
    expected = "X" if check == 10 else str(check)
    return digits[-1] == expected


def is_plain_email(value: str) -> bool:
    """True for one plain address the validator would accept as stated.

    Page parsers use this to leave an obfuscated or malformed address unknown
    instead of failing the whole refresh, and never to rewrite one.
    """

    cleaned = value.strip()
    return len(cleaned) <= MAX_EMAIL_LENGTH and _EMAIL_RE.match(cleaned) is not None


def email_status_for(email_source_url: str, profile_url: str, *, authority: str) -> str:
    """``verified`` only for an official adapter and a listing on the profile's host.

    Both conditions are required (§3.5: "officially listed and verified"). The
    host check alone is not enough: an ORCID record or a shared host such as
    ``sites.google.com`` puts a self-reported address on the same host as its
    profile. A leading ``www.`` is ignored.
    """

    if authority not in CONTACT_AUTHORITIES:
        raise DiscoveryError(
            f"contact authority {authority!r} is not one of "
            f"{', '.join(sorted(CONTACT_AUTHORITIES))}."
        )
    if authority != "official":
        return "unverified"
    return "verified" if _host(email_source_url) == _host(profile_url) else "unverified"


def contact_authority_of(adapter: object) -> str:
    """The adapter's declared contact authority; ``"none"`` when it declares none.

    A declared value outside :data:`CONTACT_AUTHORITIES` is a configuration error
    raised before any source is contacted, so a typo cannot silently change trust.
    """

    authority = getattr(adapter, "contact_authority", "none")
    if not isinstance(authority, str) or authority not in CONTACT_AUTHORITIES:
        raise DiscoveryError(
            f"faculty adapter {getattr(adapter, 'name', adapter.__class__.__name__)!r} declares "
            f"contact_authority {authority!r}; expected one of "
            f"{', '.join(sorted(CONTACT_AUTHORITIES))}."
        )
    return authority


def _validated_email(
    email: str | None, source_url: str | None, full_name: str
) -> tuple[str | None, str | None]:
    if email is None and source_url is None:
        return None, None
    if email is None:
        raise DiscoveryError(
            f"faculty {full_name!r} has an email_source_url but no email; drop the URL or "
            "pass the address exactly as the page lists it."
        )
    if source_url is None:
        raise DiscoveryError(
            f"faculty {full_name!r} has an email with no email_source_url; an address is only "
            "stored with the page that lists it."
        )
    if not isinstance(email, str):
        raise DiscoveryError(f"faculty {full_name!r} has a non-text email.")
    cleaned = email.strip()
    if not is_plain_email(cleaned):
        # Obfuscated forms such as "name [at] uni.edu" are rejected rather than
        # rewritten: rewriting an address is the first step towards guessing one.
        raise DiscoveryError(
            f"faculty {full_name!r} has an email that is not a single plain address."
        )
    local, _, domain = cleaned.rpartition("@")
    return f"{local}@{domain.casefold()}", discovery._url(source_url, "email_source_url")


def _authors(values: Sequence[str] | None, title: str) -> tuple[str, ...]:
    if values is None:
        return ()
    if isinstance(values, str):
        raise DiscoveryError(f"publication {title!r} authors must be a sequence, not a string.")
    cleaned = tuple(
        discovery._required_text(name, "publication author", discovery.MAX_NAME_LENGTH)
        for name in values
    )
    if len(cleaned) > MAX_AUTHORS:
        raise DiscoveryError(f"publication {title!r} lists more than {MAX_AUTHORS} authors.")
    # Order is meaningful and two co-authors can share a name, so duplicates stay.
    return cleaned


def _unique_publications(
    publications: Iterable[DiscoveredPublication],
) -> tuple[DiscoveredPublication, ...]:
    unique: dict[str, DiscoveredPublication] = {}
    for publication in publications:
        unique.setdefault(publication_key(publication), publication)
    return tuple(unique.values())


def _confidence(value: float | None, name: str) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise DiscoveryError(f"faculty {name!r} has a non-numeric confidence.")
    number = float(value)
    if not 0.0 <= number <= 1.0:
        raise DiscoveryError(f"faculty {name!r} has confidence {number}; expected 0.0-1.0.")
    return number


def _http_status(value: int | None) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or not 100 <= value <= 599:
        raise DiscoveryError("http_status must be an HTTP status between 100 and 599 or None.")
    return value


def _host(url: str) -> str:
    host = (urlsplit(url).hostname or "").casefold()
    return host.removeprefix("www.")


# --------------------------------------------------------------------------- #
# identity
# --------------------------------------------------------------------------- #


def normalise_profile_url(url: str) -> str:
    """Scheme-less, fragment-less, lower-case-host form used for identity.

    ``http``/``https``, a leading ``www.``, a trailing slash or a ``#section``
    anchor do not make a different person; the path and query do. The ``www.``
    rule matches :func:`email_status_for`, so identity and contact verification
    agree on what "the same host" means.
    """

    parts = urlsplit(url.strip())
    host = _host(url.strip())
    if parts.port is not None:
        host = f"{host}:{parts.port}"
    path = parts.path.rstrip("/") or "/"
    return urlunsplit(("", host, path, parts.query, ""))


def faculty_key(record: DiscoveredFaculty) -> str:
    """Canonical identity of a faculty record: its official profile URL.

    The URL is hashed rather than slugged, so ``/jane.doe`` and ``/jane-doe``
    cannot collapse into one person. The name is deliberately not part of it.
    """

    normalised = normalise_profile_url(record.profile_url)
    digest = hashlib.sha256(normalised.encode("utf-8")).hexdigest()[:32]
    return cache.canonical_key("faculty", _host(record.profile_url), digest)


def publication_key(record: DiscoveredPublication) -> str:
    """A DOI identifies a paper when stated; otherwise its source URL does."""

    if record.doi is not None:
        digest = hashlib.sha256(record.doi.encode("utf-8")).hexdigest()[:32]
        return cache.canonical_key("publication", "doi", digest)
    # The same normalisation as a profile URL (scheme, `www.`, trailing slash and
    # fragment ignored), so `http://x/p` and `https://x/p/` are one paper.
    normalised = normalise_profile_url(record.url or "")
    digest = hashlib.sha256(normalised.encode("utf-8")).hexdigest()[:32]
    return cache.canonical_key("publication", "url", digest)


# --------------------------------------------------------------------------- #
# search
# --------------------------------------------------------------------------- #


def search_faculty(
    connection: sqlite3.Connection,
    query: FacultyQuery,
    *,
    adapter: FacultyAdapter | None = None,
    offline: bool = False,
    now: datetime | None = None,
    actor: str = "discovery",
) -> FacultyResult:
    """Answer a faculty query from the cache, refreshing only when needed.

    Same contract as :func:`discovery.search_programs`: a fresh answer (including
    a recent empty one) makes no adapter call, an expired answer refreshes once,
    a failed refresh returns the previous rows labelled stale, and ``offline``
    filters local rows without any source.
    """

    checked_at = now or datetime.now(UTC)
    normalised = query.normalised()
    fingerprint = normalised.fingerprint()
    cached_ids, empty_until = _index_entry(connection, fingerprint)
    if offline:
        cached_ids = _offline_faculty_ids(connection, normalised)
    if (
        not cached_ids
        and empty_until is not None
        and checked_at < cache.parse_timestamp(empty_until)
    ):
        _audit_served(connection, actor, fingerprint, "cache", 0, stale=False, adapter=None)
        return FacultyResult(normalised, (), served_from_cache=True, stale=False, message=None)
    cached = _load_faculty(connection, cached_ids, now=checked_at)

    if cached and not _any_stale(cached):
        _audit_served(connection, actor, fingerprint, "cache", len(cached), stale=False)
        return FacultyResult(normalised, cached, served_from_cache=True, stale=False, message=None)

    if offline:
        _audit_served(connection, actor, fingerprint, "cache", len(cached), stale=bool(cached))
        message = (
            None
            if cached
            else "No cached faculty matches this query yet. Run the query online once "
            "to fill the cache."
        )
        return FacultyResult(
            normalised, cached, served_from_cache=True, stale=bool(cached), message=message
        )

    if adapter is None:
        if cached:
            _audit_served(connection, actor, fingerprint, "cache", len(cached), stale=True)
            return FacultyResult(
                normalised,
                cached,
                served_from_cache=True,
                stale=True,
                message=_stale_message(cached),
            )
        raise DiscoveryUnavailableError(
            "no faculty discovery adapter is configured, so there is no live source to "
            "query. Configure one, or browse the cache in offline mode."
        )

    if _refresh_backed_off(connection, cached, checked_at):
        _audit_served(connection, actor, fingerprint, "cache", len(cached), stale=True)
        return FacultyResult(
            normalised,
            cached,
            served_from_cache=True,
            stale=True,
            message=(
                "Showing cached faculty; the last refresh failed less than an hour ago, "
                "so the source is not retried yet. Treat them as stale, not verified."
            ),
        )

    adapter_name = getattr(adapter, "name", adapter.__class__.__name__)
    authority = contact_authority_of(adapter)
    try:
        records = tuple(
            validate_faculty(record, now=checked_at) for record in adapter.search(normalised)
        )
    except Exception as exc:  # adapter boundary: network, parsing or validation
        with db.transaction(connection):
            _record_failure(connection, cached, checked_at, exc)
            discovery._audit(
                connection,
                actor=actor,
                action="research.refresh_failed",
                outcome="failure",
                metadata={
                    "fingerprint": fingerprint,
                    "entity": "faculty",
                    "adapter": adapter_name,
                    "error": exc.__class__.__name__,
                },
            )
        if cached:
            stale = _load_faculty(connection, tuple(member.id for member in cached), now=checked_at)
            return FacultyResult(
                normalised,
                stale,
                served_from_cache=True,
                stale=True,
                message=_stale_message(stale, failed=True),
            )
        if isinstance(exc, DiscoveryError):
            raise
        raise DiscoveryError(
            f"the faculty source {adapter_name!r} failed ({exc.__class__.__name__}); "
            "the cache has no result for this query."
        ) from exc

    with db.transaction(connection):
        ids = tuple(
            dict.fromkeys(
                _store_faculty(
                    connection, record, adapter=adapter, authority=authority, now=checked_at
                )
                for record in records
            )
        )
        _write_index(connection, normalised, fingerprint, ids, now=checked_at)
        _audit_served(
            connection, actor, fingerprint, "adapter", len(ids), stale=False, adapter=adapter_name
        )

    faculty = _load_faculty(connection, ids, now=checked_at)
    return FacultyResult(normalised, faculty, served_from_cache=False, stale=False, message=None)


def discovered_faculty_matches(record: DiscoveredFaculty, query: FacultyQuery) -> bool:
    """Apply the cache filter rule to an adapter record before it is stored."""

    normalised = query.normalised()
    return _matches_values(
        interests=record.research_interests,
        country=record.country,
        institution=record.institution,
        department=record.department,
        institution_id=None,
        department_id=None,
        wanted_research_area=normalised.research_area,
        wanted_country=normalised.country,
        wanted_institution=normalised.institution,
        wanted_department=normalised.department,
        wanted_institution_id=None,
        wanted_department_id=None,
    )


# --------------------------------------------------------------------------- #
# cache-only browsing (§13.2)
# --------------------------------------------------------------------------- #


def browse_faculty(
    connection: sqlite3.Connection,
    filters: FacultyFilters | None = None,
    *,
    limit: int = discovery.DEFAULT_BROWSE_LIMIT,
    offset: int = 0,
    now: datetime | None = None,
    include_publications: bool = True,
) -> FacultyPage:
    """One page of cached faculty, sorted by name. Never calls an adapter.

    Filtering by ``institution_id`` and then ``department_id`` is the
    university → department → faculty hierarchy; it works offline.

    With ``include_publications=False`` each member carries only its
    highlighted papers and ``publication_count``, so a list page does not load
    every paper, author list and abstract just to count them.
    """

    checked_at = now or datetime.now(UTC)
    normalised = (filters or FacultyFilters()).normalised()
    limit, offset = discovery._page_bounds(limit, offset)
    matched = [
        int(row["id"])
        for row in _faculty_rows(connection)
        if _row_matches(
            row,
            research_area=normalised.research_area,
            country=normalised.country,
            institution_id=normalised.institution_id,
            department_id=normalised.department_id,
        )
    ]
    return FacultyPage(
        faculty=_load_faculty(
            connection,
            matched[offset : offset + limit],
            now=checked_at,
            include_publications=include_publications,
        ),
        total=len(matched),
        limit=limit,
        offset=offset,
    )


def public_email(member: FacultyMember) -> str | None:
    """The address a screen, API or CLI may display: verified ones only (ADR-021).

    An unverified address is never shown, so it cannot be copied into outreach
    by hand; callers show ``email_status`` and ``email_source_url`` instead.
    """

    return member.email if member.email_status == "verified" else None


def get_faculty(
    connection: sqlite3.Connection, faculty_id: int, *, now: datetime | None = None
) -> FacultyMember:
    """Return one cached faculty member or raise :class:`FacultyNotFoundError`."""

    checked_at = now or datetime.now(UTC)
    faculty_id = discovery._optional_id(faculty_id, "faculty_id") or 0
    members = _load_faculty(connection, (faculty_id,), now=checked_at)
    if not members:
        raise FacultyNotFoundError(f"faculty {faculty_id} is not in the shared cache.")
    return members[0]


# --------------------------------------------------------------------------- #
# storage
# --------------------------------------------------------------------------- #


def _store_faculty(
    connection: sqlite3.Connection,
    record: DiscoveredFaculty,
    *,
    adapter: FacultyAdapter,
    authority: str,
    now: datetime,
) -> int:
    now_iso = cache.to_timestamp(now)
    institution_id = discovery.upsert_institution(
        connection, institution=record.institution, country=record.country, now_iso=now_iso
    )
    department_id = discovery.upsert_department(
        connection,
        institution_id=institution_id,
        institution=record.institution,
        country=record.country,
        department=record.department,
        now_iso=now_iso,
    )
    key = faculty_key(record)
    row = connection.execute(
        """
        INSERT INTO faculty_members (
            canonical_key, full_name, institution_id, department_id, country, title,
            lab_name, profile_url, research_interests_json, recruitment_evidence_url,
            orcid, confidence, last_checked_at, created_at, updated_at
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(canonical_key) DO UPDATE SET
            full_name = excluded.full_name,
            institution_id = excluded.institution_id,
            department_id = excluded.department_id,
            country = excluded.country,
            title = excluded.title,
            lab_name = excluded.lab_name,
            profile_url = excluded.profile_url,
            research_interests_json = excluded.research_interests_json,
            recruitment_evidence_url = excluded.recruitment_evidence_url,
            orcid = excluded.orcid,
            confidence = excluded.confidence,
            last_checked_at = excluded.last_checked_at,
            updated_at = excluded.updated_at
        RETURNING id
        """,
        (
            key,
            record.full_name,
            institution_id,
            department_id,
            record.country,
            record.title,
            record.lab_name,
            record.profile_url,
            json.dumps(record.research_interests, ensure_ascii=False),
            record.recruitment_evidence_url,
            record.orcid,
            record.confidence,
            now_iso,
            now_iso,
            now_iso,
        ),
    ).fetchone()
    faculty_id = int(row["id"])

    # A successful refresh replaces what the page states now: a source or an
    # address the page no longer lists is not kept as if it were still verified.
    connection.execute("DELETE FROM faculty_sources WHERE faculty_id = ?", (faculty_id,))
    for source in record.sources:
        connection.execute(
            "INSERT INTO faculty_sources "
            "(faculty_id, url, publisher, kind, fetched_at, verified_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (faculty_id, source.url, source.publisher, source.kind, now_iso, now_iso),
        )
    connection.execute("DELETE FROM faculty_contacts WHERE faculty_id = ?", (faculty_id,))
    if record.email is not None and record.email_source_url is not None:
        status = email_status_for(record.email_source_url, record.profile_url, authority=authority)
        connection.execute(
            "INSERT INTO faculty_contacts "
            "(faculty_id, kind, value, source_url, status, verified_at) "
            "VALUES (?, 'email', ?, ?, ?, ?)",
            (
                faculty_id,
                record.email,
                record.email_source_url,
                status,
                now_iso if status == "verified" else None,
            ),
        )

    connection.execute("DELETE FROM faculty_publications WHERE faculty_id = ?", (faculty_id,))
    for publication in record.publications:
        publication_id = _store_publication(
            connection, publication, adapter=adapter, record=record, now_iso=now_iso
        )
        connection.execute(
            "INSERT INTO faculty_publications (faculty_id, publication_id, linked_at) "
            "VALUES (?, ?, ?)",
            (faculty_id, publication_id, now_iso),
        )

    cache.put_entry(
        connection,
        entity_type="faculty",
        entity_id=faculty_id,
        canonical_key=key,
        source_url=record.profile_url,
        fetched_at=now_iso,
        verified_at=now_iso,
        ttl_seconds=cache.ttl_seconds("faculty"),
        content_hash=cache.content_hash(record_payload(record)),
        http_status=record.http_status,
        adapter=adapter.name,
        parser_version=adapter.parser_version,
        raw_payload_path=record.raw_payload_path,
    )
    discovery.cache_institution_and_department(
        connection,
        institution=record.institution,
        country=record.country,
        department=record.department,
        institution_id=institution_id,
        department_id=department_id,
        source_url=record.profile_url,
        http_status=record.http_status,
        adapter_name=adapter.name,
        parser_version=adapter.parser_version,
        now_iso=now_iso,
    )
    return faculty_id


def _store_publication(
    connection: sqlite3.Connection,
    publication: DiscoveredPublication,
    *,
    adapter: FacultyAdapter,
    record: DiscoveredFaculty,
    now_iso: str,
) -> int:
    key = publication_key(publication)
    row = connection.execute(
        """
        INSERT INTO publications
            (canonical_key, title, year, doi, url, venue, abstract, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(canonical_key) DO UPDATE SET
            title = excluded.title,
            year = excluded.year,
            doi = excluded.doi,
            url = excluded.url,
            venue = excluded.venue,
            abstract = excluded.abstract,
            updated_at = excluded.updated_at
        RETURNING id
        """,
        (
            key,
            publication.title,
            publication.year,
            publication.doi,
            publication.url,
            publication.venue,
            publication.abstract,
            now_iso,
            now_iso,
        ),
    ).fetchone()
    publication_id = int(row["id"])
    connection.execute(
        "DELETE FROM publication_authors WHERE publication_id = ?", (publication_id,)
    )
    for position, name in enumerate(publication.authors, start=1):
        connection.execute(
            "INSERT INTO publication_authors (publication_id, position, display_name) "
            "VALUES (?, ?, ?)",
            (publication_id, position, name),
        )
    cache.put_entry(
        connection,
        entity_type="publication",
        entity_id=publication_id,
        canonical_key=key,
        source_url=publication.url or record.profile_url,
        fetched_at=now_iso,
        verified_at=now_iso,
        ttl_seconds=cache.ttl_seconds("publication"),
        content_hash=cache.content_hash(publication_payload(publication)),
        http_status=record.http_status,
        adapter=adapter.name,
        parser_version=adapter.parser_version,
    )
    return publication_id


def _write_index(
    connection: sqlite3.Connection,
    query: FacultyQuery,
    fingerprint: str,
    ids: Sequence[int],
    *,
    now: datetime,
) -> None:
    now_iso = cache.to_timestamp(now)
    payload = query.as_dict()
    empty_expiry = (
        cache.to_timestamp(now + timedelta(days=discovery.EMPTY_RESULT_TTL_DAYS))
        if not ids
        else None
    )
    # The index's `department` column holds the department filter; the
    # institution filter is part of the fingerprint only (no column for it).
    connection.execute(
        """
        INSERT INTO research_query_index (
            fingerprint, entity_type, research_area, country, department,
            degree_level, entity_ids_json, expires_at, created_at, updated_at
        ) VALUES (?, 'faculty', ?, ?, ?, NULL, ?, ?, ?, ?)
        ON CONFLICT(fingerprint) DO UPDATE SET
            research_area = excluded.research_area,
            country = excluded.country,
            department = excluded.department,
            entity_ids_json = excluded.entity_ids_json,
            expires_at = excluded.expires_at,
            updated_at = excluded.updated_at
        """,
        (
            fingerprint,
            payload["research_area"],
            payload["country"],
            payload["department"],
            json.dumps(list(ids)),
            empty_expiry,
            now_iso,
            now_iso,
        ),
    )


# --------------------------------------------------------------------------- #
# reads
# --------------------------------------------------------------------------- #


def _index_entry(
    connection: sqlite3.Connection, fingerprint: str
) -> tuple[tuple[int, ...], str | None]:
    row = connection.execute(
        "SELECT entity_ids_json, expires_at FROM research_query_index "
        "WHERE fingerprint = ? AND entity_type = 'faculty'",
        (fingerprint,),
    ).fetchone()
    if row is None:
        return (), None
    try:
        decoded = json.loads(row["entity_ids_json"])
        ids = tuple(int(value) for value in decoded)
    except (TypeError, ValueError) as exc:
        raise CacheError("the research query index holds an invalid faculty id list.") from exc
    return ids, None if row["expires_at"] is None else str(row["expires_at"])


def _faculty_rows(connection: sqlite3.Connection) -> list[sqlite3.Row]:
    return connection.execute(
        """
        SELECT f.id, f.full_name, f.country, f.research_interests_json,
               f.institution_id, f.department_id,
               i.name AS institution_name, d.name AS department_name
        FROM faculty_members AS f
        JOIN institutions AS i ON i.id = f.institution_id
        LEFT JOIN departments AS d ON d.id = f.department_id
        ORDER BY f.full_name, f.id
        """
    ).fetchall()


def _row_matches(
    row: sqlite3.Row,
    *,
    research_area: str | None = None,
    country: str | None = None,
    institution: str | None = None,
    department: str | None = None,
    institution_id: int | None = None,
    department_id: int | None = None,
) -> bool:
    return _matches_values(
        interests=discovery._json_tuple(row["research_interests_json"]),
        country=row["country"],
        institution=row["institution_name"],
        department=row["department_name"],
        institution_id=int(row["institution_id"]),
        department_id=None if row["department_id"] is None else int(row["department_id"]),
        wanted_research_area=research_area,
        wanted_country=country,
        wanted_institution=institution,
        wanted_department=department,
        wanted_institution_id=institution_id,
        wanted_department_id=department_id,
    )


def _matches_values(
    *,
    interests: Sequence[str],
    country: Any,
    institution: Any,
    department: Any,
    institution_id: int | None,
    department_id: int | None,
    wanted_research_area: str | None,
    wanted_country: str | None,
    wanted_institution: str | None,
    wanted_department: str | None,
    wanted_institution_id: int | None,
    wanted_department_id: int | None,
) -> bool:
    """The one faculty filter rule, shared by cache reads and adapter records.

    Country is an equality match; institution, department and research area
    accept a substring. A NULL field never matches a stated filter.
    """

    if wanted_institution_id is not None and institution_id != wanted_institution_id:
        return False
    if wanted_department_id is not None and department_id != wanted_department_id:
        return False
    if wanted_country is not None and not discovery._same_text(country, wanted_country):
        return False
    if wanted_institution is not None and not discovery._contains_text(
        institution, wanted_institution
    ):
        return False
    if wanted_department is not None and not discovery._contains_text(
        department, wanted_department
    ):
        return False
    if wanted_research_area is not None:
        return any(wanted_research_area in cache.normalise_text(area) for area in interests)
    return True


def _offline_faculty_ids(connection: sqlite3.Connection, query: FacultyQuery) -> tuple[int, ...]:
    return tuple(
        int(row["id"])
        for row in _faculty_rows(connection)
        if _row_matches(
            row,
            research_area=query.research_area,
            country=query.country,
            institution=query.institution,
            department=query.department,
        )
    )


def _load_faculty(
    connection: sqlite3.Connection,
    ids: Sequence[int],
    *,
    now: datetime,
    include_publications: bool = True,
) -> tuple[FacultyMember, ...]:
    if not ids:
        return ()
    rows: list[sqlite3.Row] = []
    sources: dict[int, list[str]] = {}
    contacts: dict[int, sqlite3.Row] = {}
    links: dict[int, list[int]] = {}
    entries: dict[int, tuple[cache.CacheEntry, ...]] = {}
    for batch in discovery._batches(ids):
        placeholders = ", ".join("?" for _ in batch)
        rows.extend(
            connection.execute(
                f"""
                SELECT f.*, i.name AS institution_name, d.name AS department_name
                FROM faculty_members AS f
                JOIN institutions AS i ON i.id = f.institution_id
                LEFT JOIN departments AS d ON d.id = f.department_id
                WHERE f.id IN ({placeholders})
                """,  # noqa: S608 - placeholders are generated, ids are bound
                batch,
            ).fetchall()
        )
        for row in connection.execute(
            f"SELECT faculty_id, url FROM faculty_sources WHERE faculty_id IN ({placeholders}) "
            "ORDER BY id",  # noqa: S608
            batch,
        ):
            sources.setdefault(int(row["faculty_id"]), []).append(str(row["url"]))
        for row in connection.execute(
            f"SELECT faculty_id, value, source_url, status FROM faculty_contacts "  # noqa: S608
            f"WHERE kind = 'email' AND faculty_id IN ({placeholders})",
            batch,
        ):
            contacts[int(row["faculty_id"])] = row
        # Only the sort columns are read here; the heavy columns (abstract,
        # authors) are loaded later for the papers actually returned.
        for row in connection.execute(
            f"SELECT fp.faculty_id, p.id, p.year, p.title "  # noqa: S608
            f"FROM faculty_publications AS fp "
            f"JOIN publications AS p ON p.id = fp.publication_id "
            f"WHERE fp.faculty_id IN ({placeholders})",
            batch,
        ):
            links.setdefault(int(row["faculty_id"]), []).append(
                _order_key(row["year"], str(row["title"]), int(row["id"]))
            )
        entries.update(
            cache.entries_for_entities(connection, entity_type="faculty", entity_ids=batch)
        )
    ordered: dict[int, list[int]] = {
        faculty_id: [key[-1] for key in sorted(keys)] for faculty_id, keys in links.items()
    }
    wanted = {
        faculty_id: ids_ if include_publications else ids_[:HIGHLIGHTED_PUBLICATIONS]
        for faculty_id, ids_ in ordered.items()
    }
    publications = _load_publications(
        connection,
        tuple(publication_id for linked in wanted.values() for publication_id in linked),
        now=now,
    )
    rows.sort(key=lambda row: (str(row["full_name"]), int(row["id"])))

    members: list[FacultyMember] = []
    for row in rows:
        faculty_id = int(row["id"])
        faculty_entries = entries.get(faculty_id, ())
        is_stale = not faculty_entries or any(entry.is_stale(now) for entry in faculty_entries)
        contact = contacts.get(faculty_id)
        email_status = "unavailable" if contact is None else str(contact["status"])
        papers = tuple(
            publications[pid] for pid in wanted.get(faculty_id, ()) if pid in publications
        )
        members.append(
            FacultyMember(
                id=faculty_id,
                canonical_key=str(row["canonical_key"]),
                full_name=str(row["full_name"]),
                institution=str(row["institution_name"]),
                institution_id=int(row["institution_id"]),
                department=discovery._row_text(row["department_name"]),
                department_id=(None if row["department_id"] is None else int(row["department_id"])),
                country=discovery._row_text(row["country"]),
                title=discovery._row_text(row["title"]),
                lab_name=discovery._row_text(row["lab_name"]),
                profile_url=str(row["profile_url"]),
                evidence_urls=tuple(sources.get(faculty_id, ())),
                research_interests=discovery._json_tuple(row["research_interests_json"]),
                email=None if contact is None else str(contact["value"]),
                email_status=email_status,
                email_source_url=None if contact is None else str(contact["source_url"]),
                orcid=discovery._row_text(row["orcid"]),
                contact_sendable=email_status == "verified" and not is_stale,
                recruitment_evidence_url=discovery._row_text(row["recruitment_evidence_url"]),
                publications=papers if include_publications else (),
                highlighted_publications=papers[:HIGHLIGHTED_PUBLICATIONS],
                publication_count=len(ordered.get(faculty_id, ())),
                confidence=None if row["confidence"] is None else float(row["confidence"]),
                last_checked_at=discovery._row_text(row["last_checked_at"]),
                stale=is_stale,
            )
        )
    return tuple(members)


def _load_publications(
    connection: sqlite3.Connection, ids: Sequence[int], *, now: datetime
) -> dict[int, Publication]:
    if not ids:
        return {}
    rows: list[sqlite3.Row] = []
    authors: dict[int, list[str]] = {}
    entries: dict[int, tuple[cache.CacheEntry, ...]] = {}
    for batch in discovery._batches(ids):
        placeholders = ", ".join("?" for _ in batch)
        rows.extend(
            connection.execute(
                f"SELECT * FROM publications WHERE id IN ({placeholders})",  # noqa: S608
                batch,
            ).fetchall()
        )
        for row in connection.execute(
            f"SELECT publication_id, display_name FROM publication_authors "  # noqa: S608
            f"WHERE publication_id IN ({placeholders}) ORDER BY publication_id, position",
            batch,
        ):
            authors.setdefault(int(row["publication_id"]), []).append(str(row["display_name"]))
        entries.update(
            cache.entries_for_entities(connection, entity_type="publication", entity_ids=batch)
        )
    recent_from = now.astimezone(UTC).year - (RECENT_PUBLICATION_YEARS - 1)
    loaded: dict[int, Publication] = {}
    for row in rows:
        publication_id = int(row["id"])
        publication_entries = entries.get(publication_id, ())
        latest = (
            max(entry.checked_at for entry in publication_entries) if publication_entries else None
        )
        year = None if row["year"] is None else int(row["year"])
        loaded[publication_id] = Publication(
            id=publication_id,
            canonical_key=str(row["canonical_key"]),
            title=str(row["title"]),
            year=year,
            doi=discovery._row_text(row["doi"]),
            url=str(row["url"]),
            venue=discovery._row_text(row["venue"]),
            abstract=discovery._row_text(row["abstract"]),
            authors=tuple(authors.get(publication_id, ())),
            recent=year is not None and year >= recent_from,
            last_checked_at=None if latest is None else cache.to_timestamp(latest),
            stale=not publication_entries
            or any(entry.is_stale(now) for entry in publication_entries),
        )
    return loaded


def _order_key(year: Any, title: str, publication_id: int) -> tuple[int, int, str, int]:
    """The one paper order: newest first, an undated paper after every dated one.

    It reads bare columns, so the order is decided before a paper is loaded.
    """

    year_value = None if year is None else int(year)
    undated = 1 if year_value is None else 0
    return (undated, -(year_value or 0), title.casefold(), publication_id)


# --------------------------------------------------------------------------- #
# provenance and failure helpers
# --------------------------------------------------------------------------- #


def record_payload(record: DiscoveredFaculty) -> dict[str, Any]:
    return {
        "full_name": record.full_name,
        "institution": record.institution,
        "profile_url": record.profile_url,
        "country": record.country,
        "department": record.department,
        "title": record.title,
        "lab_name": record.lab_name,
        "research_interests": list(record.research_interests),
        "email": record.email,
        "email_source_url": record.email_source_url,
        "orcid": record.orcid,
        "recruitment_evidence_url": record.recruitment_evidence_url,
        "publications": [publication_key(publication) for publication in record.publications],
        "confidence": record.confidence,
        "sources": [
            {"url": source.url, "publisher": source.publisher, "kind": source.kind}
            for source in record.sources
        ],
    }


def publication_payload(record: DiscoveredPublication) -> dict[str, Any]:
    return {
        "title": record.title,
        "url": record.url,
        "doi": record.doi,
        "year": record.year,
        "authors": list(record.authors),
        "venue": record.venue,
        "abstract": record.abstract,
    }


def _any_stale(members: Sequence[FacultyMember]) -> bool:
    return any(member.stale for member in members)


def _stale_message(members: Sequence[FacultyMember], *, failed: bool = False) -> str:
    checked = sorted(m.last_checked_at for m in members if m.last_checked_at is not None)
    since = f" last checked {checked[0]}" if checked else ""
    reason = "the last refresh failed" if failed else "the cache entry expired"
    return (
        f"Showing cached faculty{since}; {reason}. Treat them as stale, not verified, "
        "and do not use their contact details for outreach."
    )


def _record_failure(
    connection: sqlite3.Connection,
    members: Sequence[FacultyMember],
    now: datetime,
    error: Exception,
) -> None:
    """Mark every entry the failed source backs as stale; caller owns the transaction.

    Faculty, their attributed publications and the derived institution and
    department entries all refreshed from the same pages, so they fail together.
    """

    if not members:
        return
    attempted_at = cache.to_timestamp(now)
    error_name = error.__class__.__name__
    targets: dict[str, set[int]] = {
        "faculty": set(),
        "publication": set(),
        "institution": set(),
        "department": set(),
    }
    for member in members:
        targets["faculty"].add(member.id)
        targets["institution"].add(member.institution_id)
        if member.department_id is not None:
            targets["department"].add(member.department_id)
        targets["publication"].update(publication.id for publication in member.publications)
    for entity_type, entity_ids in targets.items():
        for entity_id in sorted(entity_ids):
            cache.mark_refresh_failed(
                connection,
                entity_type=entity_type,
                entity_id=entity_id,
                attempted_at=attempted_at,
                error=error_name,
            )


def _refresh_backed_off(
    connection: sqlite3.Connection, members: Sequence[FacultyMember], now: datetime
) -> bool:
    """True when a refresh of these faculty failed within the retry window."""

    if not members:
        return False
    entries = cache.entries_for_entities(
        connection, entity_type="faculty", entity_ids=tuple(member.id for member in members)
    )
    cutoff = now - timedelta(seconds=discovery.FAILED_REFRESH_RETRY_SECONDS)
    return any(
        entry.last_error is not None and cache.parse_timestamp(entry.last_attempt_at) > cutoff
        for member in members
        for entry in entries.get(member.id, ())
    )


def _audit_served(
    connection: sqlite3.Connection,
    actor: str,
    fingerprint: str,
    served_from: str,
    results: int,
    *,
    stale: bool,
    adapter: str | None = None,
) -> None:
    discovery._audit(
        connection,
        actor=actor,
        action="research.query_served",
        outcome="success",
        metadata=_served_metadata(fingerprint, served_from, results, stale, adapter),
    )


def _served_metadata(
    fingerprint: str, served_from: str, results: int, stale: bool, adapter: str | None
) -> Mapping[str, Any]:
    return {
        "fingerprint": fingerprint,
        "entity": "faculty",
        "served_from": served_from,
        "faculty": results,
        "stale": stale,
        "adapter": adapter,
    }
