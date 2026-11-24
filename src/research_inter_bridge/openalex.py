"""Attach papers to faculty records from OpenAlex, keyed only by a stated ORCID iD.

Phase 5c of ``task.md`` (§3.5, ADR-020). OpenAlex indexes works and authors but
publishes no official profile URL or institutional email, so it is never a
faculty source here. It only answers "which works list this ORCID iD as an
author?" for a person whose official page stated that iD (ADR-019).

* One polite GET per person with an ORCID iD, sequentially, through
  :class:`~research_inter_bridge.http_source.PoliteHttpClient`; a failure raises
  so the faculty refresh is marked failed instead of storing half a result.
* A work is kept only when one of its authorships carries the same ORCID iD.
  The query filter is not trusted on its own, and author *names* are never
  compared.
* Fields come only from the response: a missing DOI, venue or abstract stays
  unknown. The abstract is rebuilt word for word from OpenAlex's inverted index;
  nothing is summarised or generated.
* No contact details come from OpenAlex. The wrapper keeps the inner adapter's
  ``contact_authority``, so it can neither raise nor lower trust in an address.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import replace
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode, urlsplit

from . import discovery
from . import faculty as fac
from .errors import ConfigError, DiscoveryError
from .http_source import PoliteHttpClient, decode_text

OPENALEX_WORKS_URL = "https://api.openalex.org/works"

#: Works requested per person. Profiles highlight three; the rest give a short,
#: newest-first list without asking the index for a whole career.
WORKS_PER_PERSON = 25

#: Fields requested from OpenAlex. Asking only for these keeps responses small
#: and makes it explicit which fields this module reads.
WORK_FIELDS = (
    "id",
    "doi",
    "display_name",
    "publication_year",
    "authorships",
    "primary_location",
    "abstract_inverted_index",
)

#: OpenAlex list endpoints (``/works?filter=...``) return only a work's first 100
#: authorships, in byline order; ``is_authors_truncated`` marks that, and only the
#: single-work endpoint returns the full list (help.openalex.org, Work object).
LIST_AUTHORSHIP_LIMIT = 100

#: Length caps that match :mod:`faculty` validation. A value over its cap is left
#: unknown (or the work skipped, for a title) so one odd record cannot fail the
#: whole refresh.
MAX_TITLE_LENGTH = 1000
MAX_VENUE_LENGTH = 500

_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def works_url(orcid: str, contact_email: str) -> str:
    """The OpenAlex works query for one ORCID iD, newest first.

    ``mailto`` puts the request in OpenAlex's polite pool; it is the deployment's
    research contact (``RIB_RESEARCH_CONTACT_EMAIL``), never a student address.
    """

    query = urlencode(
        {
            "filter": f"author.orcid:{orcid}",
            "sort": "publication_year:desc",
            "per-page": str(WORKS_PER_PERSON),
            "select": ",".join(WORK_FIELDS),
            "mailto": contact_email,
        }
    )
    return f"{OPENALEX_WORKS_URL}?{query}"


def publications_from_works(
    payload: Any, orcid: str, *, now: datetime | None = None
) -> tuple[fac.DiscoveredPublication, ...]:
    """Map an OpenAlex ``/works`` response to publications attributed to ``orcid``."""

    if not isinstance(payload, Mapping):
        raise DiscoveryError("OpenAlex returned a response that is not a JSON object.")
    results = payload.get("results")
    if not isinstance(results, list):
        raise DiscoveryError("OpenAlex returned a response without a results list.")
    latest_year = (now or datetime.now(UTC)).year + 1
    publications: list[fac.DiscoveredPublication] = []
    for work in results:
        if not isinstance(work, Mapping) or not _attributed(work, orcid):
            continue
        publication = _publication(work, latest_year)
        if publication is not None:
            publications.append(publication)
        if len(publications) >= WORKS_PER_PERSON:
            break
    return tuple(publications)


class OrcidPublicationsAdapter:
    """Wrap a faculty adapter and attach OpenAlex works to records with an ORCID iD.

    Records without an iD pass through unchanged, with no request. Records the
    inner adapter already gave papers keep them; OpenAlex works are added after,
    and validation de-duplicates by DOI or normalised URL.
    """

    def __init__(
        self,
        inner: fac.FacultyAdapter,
        client: PoliteHttpClient,
        contact_email: str,
        *,
        now: datetime | None = None,
    ) -> None:
        contact = (contact_email or "").strip() if isinstance(contact_email, str) else ""
        if not fac.is_plain_email(contact):
            # An empty value would send `mailto=` and drop the polite-pool contact.
            raise ConfigError(
                "RIB_RESEARCH_CONTACT_EMAIL must be one email address before OpenAlex is queried."
            )
        self._inner = inner
        self._client = client
        self._contact = contact
        self._now = now
        self.name = f"{getattr(inner, 'name', inner.__class__.__name__)}+openalex"
        self.parser_version = f"{getattr(inner, 'parser_version', '0')}+openalex-1"
        # Validated now, so a misconfigured inner adapter fails before any request.
        self.contact_authority = fac.contact_authority_of(inner)

    def search(self, query: fac.FacultyQuery) -> tuple[fac.DiscoveredFaculty, ...]:
        records = tuple(self._inner.search(query))
        enriched: list[fac.DiscoveredFaculty] = []
        for record in records:
            if record.orcid is None:
                enriched.append(record)
                continue
            # Validate before any request: a malformed iD from the inner adapter
            # raises here instead of reaching the network.
            orcid = fac.normalise_orcid(record.orcid)
            works = self._works(orcid) if orcid is not None else ()
            enriched.append(replace(record, publications=(*record.publications, *works)))
        return tuple(enriched)

    def _works(self, orcid: str) -> tuple[fac.DiscoveredPublication, ...]:
        response = self._client.get(works_url(orcid, self._contact), accept="application/json")
        try:
            payload = json.loads(decode_text(response))
        except (json.JSONDecodeError, RecursionError) as exc:
            host = urlsplit(OPENALEX_WORKS_URL).netloc
            raise DiscoveryError(f"{host} returned a body that is not valid JSON.") from exc
        return publications_from_works(payload, orcid, now=self._now)


def _attributed(work: Mapping[str, Any], orcid: str) -> bool:
    """True when an authorship on ``work`` carries exactly ``orcid``."""

    authorships = work.get("authorships")
    if not isinstance(authorships, list):
        return False
    for authorship in authorships:
        author = authorship.get("author") if isinstance(authorship, Mapping) else None
        raw = author.get("orcid") if isinstance(author, Mapping) else None
        if not isinstance(raw, str):
            continue
        try:
            if fac.normalise_orcid(raw) == orcid:
                return True
        except DiscoveryError:
            continue
    return False


def _publication(work: Mapping[str, Any], latest_year: int) -> fac.DiscoveredPublication | None:
    title = _text(work.get("display_name"), MAX_TITLE_LENGTH)
    if title is None:
        return None
    doi = _doi(work.get("doi"))
    location = work.get("primary_location")
    location = location if isinstance(location, Mapping) else {}
    url = _http_url(location.get("landing_page_url")) or _http_url(work.get("id"))
    if url is None and doi is None:
        return None
    source = location.get("source")
    venue = (
        _text(source.get("display_name"), MAX_VENUE_LENGTH) if isinstance(source, Mapping) else None
    )
    return fac.DiscoveredPublication(
        title=title,
        url=url,
        doi=doi,
        year=_year(work.get("publication_year"), latest_year),
        authors=_authors(
            work.get("authorships"), truncated=work.get("is_authors_truncated") is True
        ),
        venue=venue,
        abstract=abstract_from_inverted_index(work.get("abstract_inverted_index")),
    )


def abstract_from_inverted_index(value: Any) -> str | None:
    """Rebuild an abstract from OpenAlex's ``{word: [positions]}`` index.

    Every position must be filled exactly once; a gap or a clash means the index
    is not a faithful copy of the text, so the abstract stays unknown rather than
    being stitched together with holes.
    """

    if not isinstance(value, Mapping) or not value:
        return None
    words: dict[int, str] = {}
    for word, positions in value.items():
        if not isinstance(word, str) or not isinstance(positions, list):
            return None
        for position in positions:
            if isinstance(position, bool) or not isinstance(position, int) or position < 0:
                return None
            if position in words:
                return None
            words[position] = word
    if sorted(words) != list(range(len(words))):
        return None
    text = " ".join(words[index] for index in range(len(words))).strip()
    if not text or len(text) > fac.MAX_ABSTRACT_LENGTH:
        return None
    return text


def _authors(value: Any, *, truncated: bool) -> tuple[str, ...]:
    """Author display names in order; unknown (empty) unless the list is complete.

    Dropping one nameless author would shift every later position, and a list
    cut at OpenAlex's 100-authorship limit is not the paper's author list, so
    neither is copied. ``truncated`` is OpenAlex's own flag; a list of exactly
    the limit is treated the same way, because ``select`` may omit the flag.
    """

    # LIST_AUTHORSHIP_LIMIT (100) is below faculty.MAX_AUTHORS (500), so this
    # check also keeps every copied list within the validator's cap.
    if truncated or not isinstance(value, list) or len(value) >= LIST_AUTHORSHIP_LIMIT:
        return ()
    names: list[str] = []
    for authorship in value:
        author = authorship.get("author") if isinstance(authorship, Mapping) else None
        name = (
            _text(author.get("display_name"), discovery.MAX_NAME_LENGTH)
            if isinstance(author, Mapping)
            else None
        )
        if name is None:
            return ()
        names.append(name)
    return tuple(names)


def _doi(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        return fac.normalise_doi(value)
    except DiscoveryError:
        return None


def _year(value: Any, latest_year: int) -> int | None:
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    return value if fac.MIN_PUBLICATION_YEAR <= value <= latest_year else None


def _text(value: Any, max_length: int) -> str | None:
    """Whitespace-collapsed text, or ``None`` when absent, over the cap or unsafe."""

    if not isinstance(value, str) or _CONTROL_RE.search(value):
        return None
    cleaned = " ".join(value.split())
    if not cleaned or len(cleaned) > max_length:
        return None
    return cleaned


def _http_url(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = value.strip()
    parts = urlsplit(cleaned)
    if parts.scheme in {"http", "https"} and parts.netloc and not parts.username:
        return cleaned
    return None
