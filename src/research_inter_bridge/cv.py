"""CV structure and versions (Phase 7a, task.md §3.7, ADR-024).

Rules enforced here:

* **One source document.** The student's uploaded CV is split into sections and
  bullet points once and stored as the *original* version. The database refuses
  any update to it (trigger in migration 12), and nothing in this module edits
  it, so the original is never silently modified.
* **Recognised headings only.** A section starts only at a known heading
  (``SECTION_ALIASES``); any other line, including an unknown heading, stays a
  line of the current section, so nothing is lost or misfiled.
* **Structured text.** Versions are stored as sections (canonical kind, heading
  as written, bullet lines). No file is rewritten; a PDF is rendered only when a
  later phase attaches an approved version (Owner, 2026-11-25).
* **No invention.** Splitting only regroups the student's own lines; no text is
  added, reworded or dropped (a line that fits no section stays under "other").
* **Private.** Every row carries ``user_id`` and every read is scoped to it.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

from . import db, documents, parsing
from .errors import CvError, CvVersionNotFoundError

#: Canonical section kinds and the headings that introduce them.
SECTION_ALIASES: dict[str, tuple[str, ...]] = {
    "summary": ("summary", "profile", "objective", "about me", "personal statement"),
    "education": ("education", "academic background", "academics", "qualifications"),
    "experience": (
        "experience",
        "work experience",
        "professional experience",
        "employment",
        "internships",
        "research experience",
    ),
    "projects": ("projects", "academic projects", "personal projects", "selected projects"),
    "skills": ("skills", "technical skills", "core skills", "tools", "technologies"),
    "coursework": ("coursework", "relevant coursework", "courses"),
    "publications": ("publications", "papers", "preprints"),
    "awards": ("awards", "honours", "honors", "achievements", "scholarships"),
    "activities": ("activities", "leadership", "volunteering", "extracurricular"),
    "languages": ("languages",),
}
_HEADING_LOOKUP = {alias: kind for kind, aliases in SECTION_ALIASES.items() for alias in aliases}
_BULLET_RE = re.compile(r"^\s*(?:[-*•–▪●]|\d+[.)])\s+")
#: A heading line is short; a long line is content even if it starts with "Skills".
MAX_HEADING_LENGTH = 40
#: Sections and bullets per CV. A real CV is far below this.
MAX_SECTIONS = 40
MAX_BULLETS_PER_SECTION = 200


@dataclass(frozen=True, slots=True)
class CvSection:
    kind: str
    #: The heading exactly as the student wrote it; ``None`` for preamble lines.
    heading: str | None
    bullets: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class CvVersion:
    id: int
    user_id: int
    document_id: int
    kind: str
    parent_version_id: int | None
    target_type: str | None
    target_id: int | None
    sections: tuple[CvSection, ...]
    content_sha256: str
    created_at: str
    approved_at: str | None


def _heading_kind(line: str) -> str | None:
    cleaned = line.strip().rstrip(":").strip()
    if not cleaned or len(cleaned) > MAX_HEADING_LENGTH:
        return None
    key = " ".join(cleaned.casefold().split())
    # Only recognised headings start a section. Guessing from capitals turned
    # skill lines ("PYTHON, SQL") and names ("ASHA RAO") into headings (QA B1 on
    # #44); an unknown heading now stays a line of the section above, so no
    # text is ever lost or misfiled as a heading.
    return _HEADING_LOOKUP.get(key)


def split_sections(text: str) -> tuple[CvSection, ...]:
    """Group the CV's own lines under its headings, without changing any line.

    A bullet marker starts a new bullet; an unmarked line continues the previous
    bullet only when that bullet was marked, otherwise it is its own line. Lines
    before the first heading form a ``preamble`` section (``heading=None``).
    """

    sections: list[tuple[str, str | None, list[str]]] = [("preamble", None, [])]
    continuing = False
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continuing = False
            continue
        kind = _heading_kind(line)
        if kind is not None:
            sections.append((kind, line.rstrip(":").strip(), []))
            continuing = False
            continue
        bullets = sections[-1][2]
        if _BULLET_RE.match(line):
            bullets.append(_BULLET_RE.sub("", line, count=1).strip())
            continuing = True
        elif continuing and bullets:
            bullets[-1] = f"{bullets[-1]} {line}"
        else:
            bullets.append(line)
            continuing = False
    for _, heading, bullets in sections:
        if len(bullets) > MAX_BULLETS_PER_SECTION:
            # Never truncate silently: dropping lines would change the CV (QA B2).
            raise CvError(
                f"the section {heading or 'before the first heading'!r} has more than "
                f"{MAX_BULLETS_PER_SECTION} lines; check that the right file was uploaded "
                "as the CV."
            )
    result = tuple(
        CvSection(kind=kind, heading=heading, bullets=tuple(bullets))
        for kind, heading, bullets in sections
        if bullets or heading is not None
    )
    if len(result) > MAX_SECTIONS:
        raise CvError(
            f"the CV has more than {MAX_SECTIONS} sections; check that the right file was "
            "uploaded as the CV."
        )
    return result


def sections_payload(sections: tuple[CvSection, ...]) -> list[dict[str, object]]:
    return [
        {"kind": section.kind, "heading": section.heading, "bullets": list(section.bullets)}
        for section in sections
    ]


def content_hash(sections: tuple[CvSection, ...]) -> str:
    return hashlib.sha256(
        json.dumps(sections_payload(sections), sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def create_original(
    connection: sqlite3.Connection,
    *,
    user_id: int,
    document_id: int,
    data_directory: Path | str,
) -> CvVersion:
    """Split the uploaded CV into its original version; idempotent per document."""

    existing = connection.execute(
        "SELECT * FROM cv_versions WHERE document_id = ? AND user_id = ? AND kind = 'original'",
        (document_id, user_id),
    ).fetchone()
    if existing is not None:
        return _row_to_version(existing)
    record, payload = documents.read_document(
        connection, document_id=document_id, user_id=user_id, data_directory=data_directory
    )
    if record.kind != "cv":
        raise CvError(
            f"document {document_id} was uploaded as {record.kind!r}; only a CV can be the "
            "source for CV versions."
        )
    text = parsing.extract_text(payload, record.media_type)
    sections = split_sections(text)
    if not any(section.bullets for section in sections):
        raise CvError("no text could be read from this CV; upload a text-based PDF, DOCX or TXT.")
    stamp = db.utc_now()
    with db.transaction(connection):
        # Two concurrent requests for the same document: the loser inserts
        # nothing and reads the winner's row (QA n1 on #44).
        cursor = connection.execute(
            "INSERT INTO cv_versions (user_id, document_id, kind, sections_json, "
            "content_sha256, created_at) VALUES (?, ?, 'original', ?, ?, ?) "
            "ON CONFLICT (document_id) WHERE kind = 'original' DO NOTHING",
            (
                user_id,
                document_id,
                json.dumps(sections_payload(sections), ensure_ascii=False),
                content_hash(sections),
                stamp,
            ),
        )
        if cursor.rowcount == 0:
            row = connection.execute(
                "SELECT * FROM cv_versions WHERE document_id = ? AND user_id = ? "
                "AND kind = 'original'",
                (document_id, user_id),
            ).fetchone()
            return _row_to_version(row)
        version_id = int(cursor.lastrowid or 0)
        db.record_audit_event(
            connection,
            actor=f"user:{user_id}",
            action="cv.original_created",
            outcome="success",
            target_type="cv_version",
            target_id=str(version_id),
            metadata={"document_id": document_id, "sections": len(sections)},
        )
    return get_version(connection, user_id=user_id, version_id=version_id)


def get_version(connection: sqlite3.Connection, *, user_id: int, version_id: int) -> CvVersion:
    row = connection.execute(
        "SELECT * FROM cv_versions WHERE id = ? AND user_id = ?", (version_id, user_id)
    ).fetchone()
    if row is None:
        raise CvVersionNotFoundError(f"CV version {version_id} does not exist for this account.")
    return _row_to_version(row)


def list_versions(connection: sqlite3.Connection, *, user_id: int) -> tuple[CvVersion, ...]:
    return tuple(
        _row_to_version(row)
        for row in connection.execute(
            "SELECT * FROM cv_versions WHERE user_id = ? ORDER BY id", (user_id,)
        )
    )


def export_payload(connection: sqlite3.Connection, user_id: int) -> list[dict[str, object]]:
    """Every CV version the student has, for the account export."""

    return [
        {
            "id": version.id,
            "document_id": version.document_id,
            "kind": version.kind,
            "parent_version_id": version.parent_version_id,
            "target_type": version.target_type,
            "target_id": version.target_id,
            "sections": sections_payload(version.sections),
            "created_at": version.created_at,
            "approved_at": version.approved_at,
        }
        for version in list_versions(connection, user_id=user_id)
    ]


def _row_to_version(row: sqlite3.Row) -> CvVersion:
    sections = tuple(
        CvSection(
            kind=str(item["kind"]),
            heading=None if item["heading"] is None else str(item["heading"]),
            bullets=tuple(str(bullet) for bullet in item["bullets"]),
        )
        for item in json.loads(row["sections_json"])
    )
    return CvVersion(
        id=int(row["id"]),
        user_id=int(row["user_id"]),
        document_id=int(row["document_id"]),
        kind=str(row["kind"]),
        parent_version_id=None
        if row["parent_version_id"] is None
        else int(row["parent_version_id"]),
        target_type=None if row["target_type"] is None else str(row["target_type"]),
        target_id=None if row["target_id"] is None else int(row["target_id"]),
        sections=sections,
        content_sha256=str(row["content_sha256"]),
        created_at=str(row["created_at"]),
        approved_at=None if row["approved_at"] is None else str(row["approved_at"]),
    )
