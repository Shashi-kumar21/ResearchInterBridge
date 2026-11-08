"""Read explicit program records from official web pages.

This is the parsing half of the live program adapter boundary. It deliberately
supports one machine-readable convention instead of guessing from prose:
schema.org JSON-LD blocks (``EducationalOccupationalProgram`` and ``JobPosting``).
A page that does not state a field leaves it ``None``; nothing is copied out of
a paragraph or inferred from a title.

The loader performs one polite GET per configured page through
:class:`~research_inter_bridge.http_source.PoliteHttpClient`. It does not decide
which query a record belongs to and does not write the cache; those stay in
``discovery`` so the filter and TTL rules have one owner.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from html.parser import HTMLParser
from typing import Any
from urllib.parse import urlsplit

from .discovery import MAX_ITEMS, DiscoveredProgram, SourceRef
from .http_source import PoliteHttpClient, SourceResponse, decode_text

#: Schema.org types that can describe an internship or a structured program.
#: A bare ``Course`` is excluded: it is not evidence of a research placement.
PROGRAM_SCHEMA_TYPES = frozenset({"EducationalOccupationalProgram", "JobPosting"})

_ALLOWED_URL_SCHEMES = frozenset({"http", "https"})


@dataclass(frozen=True, slots=True)
class ProgramPageSource:
    """One official page an operator has configured for a program adapter.

    ``institution``, ``country`` and ``department`` come from configuration,
    not from the page, so a page that omits a provider still has an explicit
    owner. They are never guessed from a hostname.
    """

    url: str
    institution: str
    country: str | None = None
    department: str | None = None


@dataclass(frozen=True, slots=True)
class ProgramPage:
    """The result of reading one configured page."""

    source: ProgramPageSource
    http_status: int
    programs: tuple[DiscoveredProgram, ...]


class _JsonLdCollector(HTMLParser):
    """Collect the contents of ``application/ld+json`` script blocks."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=False)
        self.blocks: list[str] = []
        self._collecting = False
        self._buffer: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.casefold() != "script":
            return
        values = {name.casefold(): value or "" for name, value in attrs}
        media_type = values.get("type", "").split(";", 1)[0].strip().casefold()
        if media_type == "application/ld+json":
            self._collecting = True
            self._buffer = []

    def handle_endtag(self, tag: str) -> None:
        if tag.casefold() == "script" and self._collecting:
            self.blocks.append("".join(self._buffer))
            self._collecting = False
            self._buffer = []

    def handle_data(self, data: str) -> None:
        if self._collecting:
            self._buffer.append(data)


def load_program_page(client: PoliteHttpClient, source: ProgramPageSource) -> ProgramPage:
    """Fetch one configured page and return only its explicit program records."""

    response = client.get(source.url)
    return ProgramPage(
        source=source,
        http_status=response.status,
        programs=programs_from_response(source, response),
    )


def load_program_pages(
    client: PoliteHttpClient, sources: tuple[ProgramPageSource, ...]
) -> tuple[ProgramPage, ...]:
    """Read configured pages one at a time.

    A failed page raises instead of returning a partial tuple: the caller must
    mark the refresh failed rather than present half a result as complete.
    Reading sequentially is intentional; a live adapter must not fan out.
    """

    return tuple(load_program_page(client, source) for source in sources)


def programs_from_response(
    source: ProgramPageSource, response: SourceResponse
) -> tuple[DiscoveredProgram, ...]:
    """Extract supported schema.org records from an already-fetched page."""

    collector = _JsonLdCollector()
    collector.feed(decode_text(response))
    collector.close()

    programs: list[DiscoveredProgram] = []
    for block in collector.blocks:
        try:
            payload = json.loads(block)
        except (json.JSONDecodeError, RecursionError):
            # One broken block must not hide a valid block next to it.
            continue
        for item in _iter_objects(payload):
            if not PROGRAM_SCHEMA_TYPES.intersection(_schema_types(item)):
                continue
            program = _program_from_object(item, source, response.status)
            if program is not None:
                programs.append(program)
            if len(programs) >= MAX_ITEMS:
                return tuple(programs)
    return tuple(programs)


def _iter_objects(value: Any) -> list[dict[str, Any]]:
    """Flatten a JSON-LD payload, including a top-level ``@graph`` list."""

    if isinstance(value, dict):
        graph = value.get("@graph")
        if isinstance(graph, list):
            flattened: list[dict[str, Any]] = []
            for item in graph:
                flattened.extend(_iter_objects(item))
            return flattened
        return [value]
    if isinstance(value, list):
        flattened = []
        for item in value:
            flattened.extend(_iter_objects(item))
        return flattened
    return []


def _schema_types(item: dict[str, Any]) -> set[str]:
    raw = item.get("@type")
    values = raw if isinstance(raw, list) else [raw]
    types: set[str] = set()
    for value in values:
        if not isinstance(value, str):
            continue
        token = value.rsplit("/", 1)[-1].rsplit(":", 1)[-1].strip()
        if token:
            types.add(token)
    return types


def _program_from_object(
    item: dict[str, Any], source: ProgramPageSource, http_status: int
) -> DiscoveredProgram | None:
    name = _text(item.get("name"))
    if name is None:
        return None

    provider = (
        _text(item.get("provider"))
        or _text(item.get("hiringOrganization"))
        or source.institution.strip()
    )
    if not provider:
        return None

    official_url = _http_url(item.get("url")) or source.url
    return DiscoveredProgram(
        name=name,
        institution=provider,
        official_url=official_url,
        country=source.country,
        department=source.department,
        opportunity_type=_text(item.get("employmentType")),
        degree_level=_text(item.get("educationalCredentialAwarded")),
        research_areas=_keywords(item.get("keywords")),
        eligibility=_prerequisites(
            item.get("programPrerequisites") or item.get("eligibilityToApply")
        ),
        opening_date=_iso_date(item.get("applicationStartDate")),
        closing_date=_iso_date(item.get("applicationDeadline")),
        start_date=_iso_date(item.get("startDate")),
        duration=_text(item.get("timeToComplete")),
        # Nothing below is present in these schema types in a dependable shape.
        # Leaving them unknown is the honest state; a parser for a source that
        # states one explicitly can pass it to this record instead.
        application_method=None,
        stipend=None,
        tuition=None,
        accommodation=None,
        required_documents=(),
        status="unverified",
        confidence=None,
        sources=(SourceRef(url=source.url, publisher=source.institution, kind="official_page"),),
        http_status=http_status,
    )


def _text(value: Any) -> str | None:
    """First useful string from a scalar or a schema.org object."""

    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    if isinstance(value, dict):
        for key in ("name", "legalName", "@value"):
            found = _text(value.get(key))
            if found is not None:
                return found
    if isinstance(value, list):
        for item in value:
            found = _text(item)
            if found is not None:
                return found
    return None


def _http_url(value: Any) -> str | None:
    raw = _text(value)
    if raw is None:
        return None
    parts = urlsplit(raw)
    if parts.scheme in _ALLOWED_URL_SCHEMES and parts.netloc:
        return raw
    return None


def _iso_date(value: Any) -> str | None:
    """Keep an explicitly stated ISO date; never parse prose or invent a day."""

    raw = _text(value)
    if raw is None or len(raw) < 10:
        return None
    candidate = raw[:10]
    try:
        parsed = date.fromisoformat(candidate)
    except ValueError:
        return None
    return parsed.isoformat()


def _keywords(value: Any) -> tuple[str, ...]:
    """Read comma-separated keywords or a list, de-duplicated in source order."""

    if isinstance(value, str):
        raw_items = value.split(",")
    elif isinstance(value, list):
        raw_items = value
    else:
        raw_items = [value]

    seen: dict[str, None] = {}
    for item in raw_items:
        text = _text(item)
        if text is not None:
            seen.setdefault(text, None)
    return tuple(seen)


def _prerequisites(value: Any) -> tuple[str, ...]:
    """Keep a stated prerequisite sentence intact; do not split it on commas."""

    if isinstance(value, str):
        text = value.strip()
        return (text,) if text else ()
    if isinstance(value, list):
        seen: dict[str, None] = {}
        for item in value:
            text = _text(item)
            if text is not None:
                seen.setdefault(text, None)
        return tuple(seen)
    text = _text(value)
    return (text,) if text is not None else ()
