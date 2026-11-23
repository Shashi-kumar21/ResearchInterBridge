"""Read explicit faculty records from official directory and profile pages.

The faculty counterpart of :mod:`program_pages` (ADR-017, ADR-019). It supports
one machine-readable convention: schema.org JSON-LD ``Person`` objects, either
top-level, inside ``@graph``, or as the ``mainEntity``/``about`` of a
``ProfilePage``. Nothing is read from prose.

* ``email`` is taken only from the ``email`` property, with an optional
  ``mailto:`` prefix removed. An obfuscated or malformed value is left unknown,
  never rewritten, and no address is ever derived from a name.
* The page that states an address is its ``email_source_url``; whether it is
  verified is decided by :func:`faculty.email_status_for`, not here.
* An ORCID iD is read only from ``identifier``/``sameAs`` values that are ORCID
  URLs or ``orcid``-typed property values, and only when its check digit holds.
* Papers are not read from pages; they come from a scholarly source keyed by the
  stated ORCID iD in a later slice.

Loading is one polite GET per configured page through
:class:`~research_inter_bridge.http_source.PoliteHttpClient`, sequentially, and a
failed page raises so the caller can mark the refresh failed.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from . import faculty as fac
from .discovery import SourceRef
from .errors import DiscoveryError
from .http_source import PoliteHttpClient, SourceResponse, decode_text
from .program_pages import (
    _http_url,
    _iter_objects,
    _JsonLdCollector,
    _keywords,
    _schema_types,
    _text,
)

#: A department directory can list many people; the cap keeps one page from
#: producing an unbounded batch for a single refresh.
MAX_PEOPLE_PER_PAGE = 200

PERSON_SCHEMA_TYPES = frozenset({"Person"})
PROFILE_PAGE_TYPES = frozenset({"ProfilePage"})


@dataclass(frozen=True, slots=True)
class FacultyPageSource:
    """One official faculty page configured by an operator.

    ``institution``, ``country`` and ``department`` are configuration, never
    guessed from a hostname. A person's own ``worksFor``/``affiliation`` is used
    only when no institution is configured.
    """

    url: str
    institution: str
    country: str | None = None
    department: str | None = None


@dataclass(frozen=True, slots=True)
class FacultyPage:
    source: FacultyPageSource
    http_status: int
    faculty: tuple[fac.DiscoveredFaculty, ...]


def load_faculty_page(client: PoliteHttpClient, source: FacultyPageSource) -> FacultyPage:
    """Fetch one configured page and return only its explicit person records."""

    response = client.get(source.url)
    return FacultyPage(
        source=source,
        http_status=response.status,
        faculty=faculty_from_response(source, response),
    )


def load_faculty_pages(
    client: PoliteHttpClient, sources: tuple[FacultyPageSource, ...]
) -> tuple[FacultyPage, ...]:
    """Read configured pages one at a time; a failed page raises (no partial result)."""

    return tuple(load_faculty_page(client, source) for source in sources)


class OfficialPageFacultyAdapter:
    """A live :class:`faculty.FacultyAdapter` over configured official pages.

    Filtering uses :func:`faculty.discovered_faculty_matches`, the same rule the
    cache read uses, so a live answer and a cached answer cannot disagree.
    """

    name = "official_faculty_page"
    parser_version = "1"
    #: Operator-configured institutional pages are the one source allowed to
    #: verify an address (ADR-018); the host rule still applies on top.
    contact_authority = "official"

    def __init__(self, client: PoliteHttpClient, sources: tuple[FacultyPageSource, ...]) -> None:
        if not sources:
            raise DiscoveryError(
                "an official faculty-page adapter needs at least one configured source URL."
            )
        self._client = client
        self._sources = tuple(sources)

    @property
    def sources(self) -> tuple[FacultyPageSource, ...]:
        return self._sources

    def search(self, query: fac.FacultyQuery) -> tuple[fac.DiscoveredFaculty, ...]:
        pages = load_faculty_pages(self._client, self._sources)
        records = tuple(person for page in pages for person in page.faculty)
        return tuple(record for record in records if fac.discovered_faculty_matches(record, query))


def faculty_from_response(
    source: FacultyPageSource, response: SourceResponse
) -> tuple[fac.DiscoveredFaculty, ...]:
    """Extract schema.org ``Person`` records from an already-fetched page."""

    collector = _JsonLdCollector()
    collector.feed(decode_text(response))
    collector.close()

    candidates: list[tuple[dict[str, Any], bool]] = []
    for block in collector.blocks:
        try:
            payload = json.loads(block)
        except (json.JSONDecodeError, RecursionError):
            # One broken block must not hide a valid block next to it.
            continue
        candidates.extend(_people(payload))

    # A Person without its own `url` may borrow the page URL as its identity only
    # when that cannot collide: the page describes exactly one person, or exactly
    # one ProfilePage main entity. On a directory page every url-less person
    # would share one canonical key, so a later refresh in a different order
    # would "rename" a stored row to someone else (QA M1, PR #24). Such people
    # are skipped rather than given an invented identity.
    distinct = {_fingerprint(item) for item, _ in candidates}
    main_entities = {_fingerprint(item) for item, is_main in candidates if is_main}
    page_identity_for: str | None = None
    if len(distinct) == 1:
        page_identity_for = next(iter(distinct))
    elif len(main_entities) == 1:
        page_identity_for = next(iter(main_entities))

    people: list[fac.DiscoveredFaculty] = []
    seen: set[str] = set()
    for item, _ in candidates:
        person = _person_from_object(
            item,
            source,
            response.status,
            may_use_page_url=_fingerprint(item) == page_identity_for,
        )
        if person is None:
            continue
        key = fac.normalise_profile_url(person.profile_url)
        if key in seen:
            # The same profile stated twice (for example a ProfilePage and its
            # Person node) is one person.
            continue
        seen.add(key)
        people.append(person)
        if len(people) >= MAX_PEOPLE_PER_PAGE:
            return tuple(people)
    return tuple(people)


def _people(payload: Any) -> list[tuple[dict[str, Any], bool]]:
    """Every ``Person`` object, flagged ``True`` when it is a ProfilePage's subject."""

    found: list[tuple[dict[str, Any], bool]] = []
    for item in _iter_objects(payload):
        types = _schema_types(item)
        if PERSON_SCHEMA_TYPES.intersection(types):
            found.append((item, False))
        elif PROFILE_PAGE_TYPES.intersection(types):
            for key in ("mainEntity", "about"):
                for entity in _iter_objects(item.get(key)):
                    if PERSON_SCHEMA_TYPES.intersection(_schema_types(entity)):
                        found.append((entity, True))
    return found


def _fingerprint(item: dict[str, Any]) -> str:
    """Content identity of a JSON-LD node, so a Person stated twice counts once."""

    return json.dumps(item, sort_keys=True, ensure_ascii=False, default=str)


def _person_from_object(
    item: dict[str, Any],
    source: FacultyPageSource,
    http_status: int,
    *,
    may_use_page_url: bool,
) -> fac.DiscoveredFaculty | None:
    name = _text(item.get("name")) or _joined_name(item)
    if name is None:
        return None
    # The configured institution wins: a page's `worksFor` is often a department
    # or lab name, and using it would split one university into several browse
    # nodes. The page value is only a fallback when nothing is configured.
    institution = (
        source.institution.strip() or _text(item.get("worksFor")) or _text(item.get("affiliation"))
    )
    if not institution:
        return None
    profile_url = _http_url(item.get("url"))
    if profile_url is None:
        if not may_use_page_url:
            return None
        profile_url = source.url
    email = _email(item.get("email"))
    return fac.DiscoveredFaculty(
        full_name=name,
        institution=institution,
        profile_url=profile_url,
        country=source.country,
        department=source.department,
        title=_text(item.get("jobTitle")),
        lab_name=None,
        research_interests=_keywords(item.get("knowsAbout")),
        email=email,
        # The address was read from this page, so this page is its evidence.
        email_source_url=source.url if email is not None else None,
        orcid=_orcid(item),
        recruitment_evidence_url=None,
        publications=(),
        confidence=None,
        sources=_sources(profile_url, source),
        http_status=http_status,
    )


def _joined_name(item: dict[str, Any]) -> str | None:
    """``givenName`` + ``familyName`` when both are stated; never one alone."""

    given = _text(item.get("givenName"))
    family = _text(item.get("familyName"))
    if given is None or family is None:
        return None
    return f"{given} {family}"


def _email(value: Any) -> str | None:
    raw = value if isinstance(value, str) else None
    if raw is None:
        return None
    cleaned = raw.strip()
    if cleaned.casefold().startswith("mailto:"):
        cleaned = cleaned[len("mailto:") :].split("?", 1)[0].strip()
    return cleaned if cleaned and fac.is_plain_email(cleaned) else None


def _orcid(item: dict[str, Any]) -> str | None:
    """The first ORCID iD stated in ``identifier`` or ``sameAs`` with a valid check digit."""

    for candidate in _orcid_candidates(item.get("identifier")) + _orcid_candidates(
        item.get("sameAs")
    ):
        try:
            return fac.normalise_orcid(candidate)
        except DiscoveryError:
            continue
    return None


def _orcid_candidates(value: Any) -> list[str]:
    values = value if isinstance(value, list) else [value]
    candidates: list[str] = []
    for entry in values:
        if isinstance(entry, str):
            if "orcid.org/" in entry.casefold():
                candidates.append(entry)
        elif isinstance(entry, dict):
            property_id = _text(entry.get("propertyID"))
            raw = entry.get("value")
            if isinstance(raw, str) and (
                (property_id is not None and property_id.casefold() == "orcid")
                or "orcid.org/" in raw.casefold()
            ):
                candidates.append(raw)
    return candidates


def _sources(profile_url: str, source: FacultyPageSource) -> tuple[SourceRef, ...]:
    # A Person's own `url` can point off the configured host (a personal site).
    # It is labelled `linked_page` then, so provenance never overstates it.
    kind = "official_page" if _same_host(profile_url, source.url) else "linked_page"
    refs = [SourceRef(url=profile_url, publisher=source.institution, kind=kind)]
    if source.url != profile_url:
        refs.append(SourceRef(url=source.url, publisher=source.institution, kind="directory_page"))
    return tuple(refs)


def _same_host(first: str, second: str) -> bool:
    def host(url: str) -> str:
        return (urlsplit(url).hostname or "").casefold().removeprefix("www.")

    return host(first) == host(second)
