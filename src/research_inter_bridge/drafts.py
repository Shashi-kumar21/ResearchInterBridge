"""Professor-specific email drafts from a template and verified facts (Phase 8a).

task.md §3.8, ADR-026. Rules enforced here:

* **Verified facts only.** The template fills in the professor's name and stated
  title, one attributed paper (title, year, link) or a stated research
  interest, the student's confirmed profile (name, degree, institution,
  internship dates) and at most two of the student's own CV lines that match
  the professor. Nothing is guessed: a missing or unverified detail is left out
  and listed as a flag.
* **Careful wording.** The template never claims something the student did not
  confirm ("your paper ... caught my interest", not "I read" or "I loved").
* **Plain text.** Subject and body are plain text; HTML is never produced or
  stored here, and the screen escapes everything it shows.
* **Versioned and bounded.** Every generation or edit is a new version; only
  the last :data:`MAX_VERSIONS` are kept.
* **Private.** Per-user rows, cascade on account deletion, in the account export;
  audit rows carry ids and hashes only, never subject or body text.
* **No sending.** Nothing here sends email; approval is Phase 8c, sending
  Phase 10.
"""

from __future__ import annotations

import hashlib
import json
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime

from . import cv, db, profiles
from . import faculty as faculty_service
from .cv_suggestions import _line_match
from .errors import CvVersionNotFoundError, DraftError
from .matching import _phrase_credit, _phrases, _tokens

MAX_SUBJECT_LENGTH = 200
MAX_BODY_LENGTH = 10_000
#: Versions kept per draft; older ones are pruned on each new version.
MAX_VERSIONS = 20
#: CV lines quoted in the research paragraph.
MAX_EVIDENCE_LINES = 2

_DEGREE_LABELS = {
    "btech": "B.Tech",
    "be": "B.E.",
    "bs": "B.S.",
    "msc": "M.Sc.",
    "mtech": "M.Tech",
    "dual_degree": "dual-degree",
    "phd": "PhD",
    "other": "",
}


@dataclass(frozen=True, slots=True)
class DraftVersion:
    id: int
    version_no: int
    source: str
    subject: str
    body: str
    recipient: str | None
    cv_version_id: int | None
    #: Every professor-specific claim and the page it came from.
    evidence: tuple[dict[str, str], ...]
    #: Missing or unverified details, so the student is warned, not misled.
    flags: tuple[str, ...]
    body_norm_sha256: str
    content_sha256: str
    created_at: str


@dataclass(frozen=True, slots=True)
class Draft:
    id: int
    target_type: str
    target_id: int
    target_name: str
    created_at: str
    updated_at: str
    current: DraftVersion
    #: Newest first, at most :data:`MAX_VERSIONS`.
    history: tuple[DraftVersion, ...]


# --------------------------------------------------------------------------- #
# public API
# --------------------------------------------------------------------------- #


def generate(
    connection: sqlite3.Connection,
    *,
    user_id: int,
    faculty_id: int,
    cv_version_id: int | None = None,
    now: datetime | None = None,
) -> Draft:
    """Create (or regenerate) the draft for one professor as a new version."""

    moment = now or datetime.now(UTC)
    profile = profiles.load_profile(connection, user_id)
    member = faculty_service.get_faculty(connection, faculty_id, now=moment)
    attachment = _attachment(connection, user_id, faculty_id, cv_version_id)
    subject, body, evidence, flags = compose(profile, member, attachment)
    recipient = faculty_service.public_email(member) if member.contact_sendable else None
    if recipient is None:
        flags.append(
            "No verified, current email for this professor; the draft cannot be sent "
            "until one is verified."
        )
    return _store(
        connection,
        user_id=user_id,
        faculty_id=faculty_id,
        source="generated",
        subject=subject,
        body=body,
        recipient=recipient,
        cv_version_id=attachment.id if attachment else None,
        evidence=evidence,
        flags=flags,
    )


def edit(
    connection: sqlite3.Connection,
    *,
    user_id: int,
    draft_id: int,
    subject: str,
    body: str,
) -> Draft:
    """Save the student's own subject and body as a new version."""

    draft = get_draft(connection, user_id=user_id, draft_id=draft_id)
    clean_subject = " ".join(subject.split())
    clean_body = _clean_body(body)
    if not clean_subject or not clean_body:
        raise DraftError("subject and body must not be empty.")
    if len(clean_subject) > MAX_SUBJECT_LENGTH:
        raise DraftError(f"subject must be at most {MAX_SUBJECT_LENGTH} characters.")
    if len(clean_body) > MAX_BODY_LENGTH:
        raise DraftError(f"body must be at most {MAX_BODY_LENGTH} characters.")
    current = draft.current
    if clean_subject == current.subject and clean_body == current.body:
        return draft
    return _store(
        connection,
        user_id=user_id,
        faculty_id=draft.target_id,
        source="edited",
        subject=clean_subject,
        body=clean_body,
        recipient=current.recipient,
        cv_version_id=current.cv_version_id,
        evidence=list(current.evidence),
        flags=list(current.flags),
    )


def get_draft(connection: sqlite3.Connection, *, user_id: int, draft_id: int) -> Draft:
    row = connection.execute(
        "SELECT * FROM email_drafts WHERE id = ? AND user_id = ?", (draft_id, user_id)
    ).fetchone()
    if row is None:
        raise DraftError(f"draft {draft_id} does not exist for this account.", code="draft_missing")
    versions = tuple(
        _row_to_version(item)
        for item in connection.execute(
            "SELECT * FROM draft_versions WHERE draft_id = ? AND user_id = ? "
            "ORDER BY version_no DESC",
            (draft_id, user_id),
        )
    )
    name_row = connection.execute(
        "SELECT full_name FROM faculty_members WHERE id = ?", (int(row["target_id"]),)
    ).fetchone()
    return Draft(
        id=int(row["id"]),
        target_type=str(row["target_type"]),
        target_id=int(row["target_id"]),
        target_name="a professor no longer in the cache" if name_row is None else str(name_row[0]),
        created_at=str(row["created_at"]),
        updated_at=str(row["updated_at"]),
        current=versions[0],
        history=versions,
    )


def list_drafts(connection: sqlite3.Connection, *, user_id: int) -> tuple[Draft, ...]:
    return tuple(
        get_draft(connection, user_id=user_id, draft_id=int(row["id"]))
        for row in connection.execute(
            "SELECT id FROM email_drafts WHERE user_id = ? ORDER BY updated_at DESC, id DESC",
            (user_id,),
        )
    )


def duplicates(connection: sqlite3.Connection, *, user_id: int, draft_id: int) -> tuple[int, ...]:
    """Other drafts of this student whose current body is the same (normalised).

    One indexed lookup on ``(user_id, body_norm_sha256)``; the 8b lint blocks
    approval when this is not empty (§14.2).
    """

    draft = get_draft(connection, user_id=user_id, draft_id=draft_id)
    rows = connection.execute(
        "SELECT DISTINCT v.draft_id FROM draft_versions AS v "
        "WHERE v.user_id = ? AND v.body_norm_sha256 = ? AND v.draft_id <> ? "
        "AND v.version_no = "
        "(SELECT MAX(version_no) FROM draft_versions WHERE draft_id = v.draft_id)",
        (user_id, draft.current.body_norm_sha256, draft_id),
    ).fetchall()
    return tuple(int(row[0]) for row in rows)


def export_payload(connection: sqlite3.Connection, user_id: int) -> list[dict[str, object]]:
    """Every draft with its kept versions, for the account export."""

    return [
        {
            "id": draft.id,
            "target_type": draft.target_type,
            "target_id": draft.target_id,
            "versions": [
                {
                    "version_no": version.version_no,
                    "source": version.source,
                    "subject": version.subject,
                    "body": version.body,
                    "recipient": version.recipient,
                    "cv_version_id": version.cv_version_id,
                    "flags": list(version.flags),
                    "created_at": version.created_at,
                }
                for version in draft.history
            ],
        }
        for draft in list_drafts(connection, user_id=user_id)
    ]


# --------------------------------------------------------------------------- #
# template
# --------------------------------------------------------------------------- #


def compose(
    profile: profiles.StudentProfile,
    member: faculty_service.FacultyMember,
    attachment: cv.CvVersion | None,
) -> tuple[str, str, list[dict[str, str]], list[str]]:
    """Subject, body, evidence and flags from verified facts only."""

    evidence: list[dict[str, str]] = []
    flags: list[str] = []
    keywords = _phrases(profile.research_keywords)

    paper = _relevant_paper(member, keywords)
    topic = _relevant_topic(member, keywords)
    if paper is not None:
        year = f" ({paper.year})" if paper.year else ""
        hook = f'Your paper "{paper.title}"{year} caught my interest'
        subject_topic = paper.title
        evidence.append({"claim": f"Paper: {paper.title}", "source": paper.url})
    elif topic is not None:
        hook = f"Your work on {topic} caught my interest"
        subject_topic = topic
        evidence.append({"claim": f"Research interest: {topic}", "source": member.profile_url})
        flags.append(
            "No attributed paper for this professor; the draft refers to a research topic."
        )
    else:
        hook = "Your group's research caught my interest"
        subject_topic = "research internship"
        flags.append(
            "No paper or research interest is stated for this professor; add a specific "
            "reference before approving."
        )

    salutation = (
        f"Dear Professor {member.full_name},"
        if member.title and "professor" in member.title.casefold()
        else f"Dear {member.full_name},"
    )
    evidence.append({"claim": f"Name: {member.full_name}", "source": member.profile_url})

    intro = _introduction(profile, flags)
    lines = _matching_lines(attachment, member, keywords)
    if lines:
        joined = "\n".join(f"- {line}" for line in lines)
        alignment = f"It connects with work I have done:\n{joined}"
        for line in lines:
            evidence.append({"claim": f"Your CV: {line}", "source": "your approved CV"})
    else:
        alignment = ""
        flags.append("No line of your CV matched this professor; add your own sentence on fit.")

    period = _period(profile)
    ask = f"Would you consider me for a research internship in your group{period}?" + (
        " My CV is attached." if attachment else ""
    )
    name = profile.preferred_name or profile.full_name
    parts = [
        salutation,
        intro,
        f"{hook}." + (f" {alignment}" if alignment and "\n" not in alignment else ""),
    ]
    if alignment and "\n" in alignment:
        parts.append(alignment)
    parts.extend(
        [ask, "Thank you for your time.", f"Kind regards,\n{name}\n{profile.contact_email}"]
    )
    body = "\n\n".join(part for part in parts if part)
    subject = _clip(f"Research internship inquiry: {subject_topic}", MAX_SUBJECT_LENGTH)
    if attachment is None:
        flags.append("No CV version is attached; choose your original or an approved version.")
    return subject, body, evidence, flags


def _introduction(profile: profiles.StudentProfile, flags: list[str]) -> str:
    name = profile.preferred_name or profile.full_name
    degree = _DEGREE_LABELS.get(profile.degree_type.value, "") if profile.degree_type else ""
    pieces = [f"My name is {name}"]
    if degree and profile.institution:
        pieces.append(f", a {degree} student at {profile.institution}")
    elif profile.institution:
        pieces.append(f", a student at {profile.institution}")
    elif degree:
        pieces.append(f", a {degree} student")
    else:
        flags.append("Your profile has no degree or institution; the introduction is minimal.")
    if profile.department and profile.institution:
        pieces.append(f" ({profile.department})")
    return "".join(pieces) + "."


def _period(profile: profiles.StudentProfile) -> str:
    if profile.internship_start and profile.internship_end:
        return f" from {_month(profile.internship_start)} to {_month(profile.internship_end)}"
    return ""


def _month(value: date) -> str:
    return value.strftime("%B %Y")


def _relevant_paper(
    member: faculty_service.FacultyMember, keywords: Sequence[str]
) -> faculty_service.Publication | None:
    papers = list(member.publications) or list(member.highlighted_publications)
    for paper in papers:  # newest first already
        title = " ".join(_tokens(paper.title))
        if any(_phrase_credit(keyword, (title,))[0] for keyword in keywords):
            return paper
    return papers[0] if papers else None


def _relevant_topic(member: faculty_service.FacultyMember, keywords: Sequence[str]) -> str | None:
    for interest in member.research_interests:
        phrase = " ".join(_tokens(interest))
        if any(_phrase_credit(keyword, (phrase,))[0] for keyword in keywords):
            return interest
    return member.research_interests[0] if member.research_interests else None


def _matching_lines(
    attachment: cv.CvVersion | None,
    member: faculty_service.FacultyMember,
    keywords: Sequence[str],
) -> list[str]:
    if attachment is None:
        return []
    terms = _phrases((*member.research_interests, *(p.title for p in member.publications)))
    scored: list[tuple[float, int, str]] = []
    position = 0
    for section in attachment.sections:
        if section.kind not in ("experience", "projects", "publications"):
            continue
        for line in section.bullets:
            score, _ = _line_match(line, terms)
            if score:
                scored.append((-score, position, line))
            position += 1
    return [line for _, _, line in sorted(scored)[:MAX_EVIDENCE_LINES]]


def _attachment(
    connection: sqlite3.Connection, user_id: int, faculty_id: int, cv_version_id: int | None
) -> cv.CvVersion | None:
    """The chosen CV version, or the approved one for this professor, or the original.

    A tailored version for another target is refused: it was approved for
    someone else (§3.7, ADR-026).
    """

    if cv_version_id is not None:
        version = cv.get_version(connection, user_id=user_id, version_id=cv_version_id)
        if version.kind == "tailored" and (
            version.target_type != "faculty" or version.target_id != faculty_id
        ):
            raise DraftError(
                "that CV version was approved for a different professor or program; "
                "attach your original or the version approved for this professor.",
                code="draft_wrong_cv",
            )
        return version
    rows = connection.execute(
        "SELECT id, kind FROM cv_versions WHERE user_id = ? AND ("
        "(kind = 'tailored' AND target_type = 'faculty' AND target_id = ?) OR kind = 'original'"
        ") ORDER BY kind = 'tailored' DESC, id DESC LIMIT 1",
        (user_id, faculty_id),
    ).fetchone()
    if rows is None:
        return None
    try:
        return cv.get_version(connection, user_id=user_id, version_id=int(rows["id"]))
    except CvVersionNotFoundError:
        return None


# --------------------------------------------------------------------------- #
# storage
# --------------------------------------------------------------------------- #


def _clean_body(body: str) -> str:
    lines = [line.rstrip() for line in body.replace("\r\n", "\n").replace("\r", "\n").split("\n")]
    return "\n".join(lines).strip()


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def normalised_body_hash(body: str) -> str:
    """Case-, whitespace- and punctuation-insensitive hash for the duplicate check."""

    return hashlib.sha256(" ".join(_tokens(body)).encode("utf-8")).hexdigest()


def content_hash(subject: str, body: str, recipient: str | None, cv_version_id: int | None) -> str:
    """The exact content an approval will bind to (8c)."""

    payload = json.dumps([subject, body, recipient, cv_version_id], ensure_ascii=False)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _store(
    connection: sqlite3.Connection,
    *,
    user_id: int,
    faculty_id: int,
    source: str,
    subject: str,
    body: str,
    recipient: str | None,
    cv_version_id: int | None,
    evidence: list[dict[str, str]],
    flags: list[str],
) -> Draft:
    stamp = db.utc_now()
    content = content_hash(subject, body, recipient, cv_version_id)
    with db.transaction(connection):
        connection.execute(
            "INSERT INTO email_drafts (user_id, target_type, target_id, created_at, updated_at) "
            "VALUES (?, 'faculty', ?, ?, ?) ON CONFLICT (user_id, target_type, target_id) "
            "DO UPDATE SET updated_at = excluded.updated_at",
            (user_id, faculty_id, stamp, stamp),
        )
        draft_id = int(
            connection.execute(
                "SELECT id FROM email_drafts WHERE user_id = ? AND target_type = 'faculty' "
                "AND target_id = ?",
                (user_id, faculty_id),
            ).fetchone()[0]
        )
        next_no = int(
            connection.execute(
                "SELECT COALESCE(MAX(version_no), 0) + 1 FROM draft_versions WHERE draft_id = ?",
                (draft_id,),
            ).fetchone()[0]
        )
        connection.execute(
            "INSERT INTO draft_versions (draft_id, user_id, version_no, source, subject, body, "
            "recipient, cv_version_id, evidence_json, flags_json, body_norm_sha256, "
            "content_sha256, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                draft_id,
                user_id,
                next_no,
                source,
                subject,
                body,
                recipient,
                cv_version_id,
                json.dumps(evidence, ensure_ascii=False),
                json.dumps(list(dict.fromkeys(flags)), ensure_ascii=False),
                normalised_body_hash(body),
                content,
                stamp,
            ),
        )
        connection.execute(
            "DELETE FROM draft_versions WHERE draft_id = ? AND version_no <= ?",
            (draft_id, next_no - MAX_VERSIONS),
        )
        db.record_audit_event(
            connection,
            actor=f"user:{user_id}",
            action=f"draft.{source}",
            outcome="success",
            target_type="email_draft",
            target_id=str(draft_id),
            metadata={"version_no": next_no, "content_sha256": content},
        )
    return get_draft(connection, user_id=user_id, draft_id=draft_id)


def _row_to_version(row: sqlite3.Row) -> DraftVersion:
    return DraftVersion(
        id=int(row["id"]),
        version_no=int(row["version_no"]),
        source=str(row["source"]),
        subject=str(row["subject"]),
        body=str(row["body"]),
        recipient=None if row["recipient"] is None else str(row["recipient"]),
        cv_version_id=None if row["cv_version_id"] is None else int(row["cv_version_id"]),
        evidence=tuple(json.loads(row["evidence_json"])),
        flags=tuple(json.loads(row["flags_json"])),
        body_norm_sha256=str(row["body_norm_sha256"]),
        content_sha256=str(row["content_sha256"]),
        created_at=str(row["created_at"]),
    )
