"""Rule-based, professor-specific CV suggestions (Phase 7b, task.md §3.7, ADR-024).

Rules enforced here:

* **No invention.** A suggestion only reorders the student's own sections or
  bullets, surfaces a skill, keyword or course the student already confirmed in
  their profile, or flags a bullet with no concrete result. No sentence is
  written for the student; the LLM layer stays off (Owner, 2026-11-25).
* **Evidence for every suggestion.** Each one names the target terms it
  matched and where the student's side comes from (a CV line or a profile
  field).
* **Nothing changes yet.** Suggestions are stored as ``pending``; the CV
  version is untouched. Accepting and approving is Phase 7c.
* **Private and cheap.** Runs are per-user, use cached public data only, make
  no network call, and are reused while the CV version, the target's
  last-checked time, the profile fields used and :data:`RULES_VERSION` are
  unchanged.
"""

from __future__ import annotations

import hashlib
import json
import re
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime

from . import cv, db, discovery, profiles
from . import faculty as faculty_service
from .errors import CvError
from .matching import _phrase_credit, _phrases, _tokens

#: Bump when a rule changes, so stored runs say which rules produced them.
RULES_VERSION = "1"

#: Sections that may be reordered. The preamble (name, contact) and education
#: keep their place: moving them would surprise a reader more than it helps.
MOVABLE_SECTIONS = frozenset(
    {"experience", "projects", "publications", "skills", "coursework", "awards", "activities"}
)

#: Phrases that usually mark a duty, not a result.
_WEAK_OPENERS = (
    "worked on",
    "helped",
    "responsible for",
    "involved in",
    "assisted",
    "participated",
)
_HAS_RESULT = re.compile(r"\d|%")
#: A suggestion list longer than this is noise, not help.
MAX_SUGGESTIONS = 30


@dataclass(frozen=True, slots=True)
class Suggestion:
    id: int
    position: int
    kind: str
    #: Section index the suggestion is about (``None`` for whole-CV ones).
    section: int | None
    bullet: int | None
    #: The student's content as it is now, and as it would be if accepted.
    before: tuple[str, ...]
    after: tuple[str, ...]
    reason: str
    evidence: tuple[str, ...]
    status: str = "pending"
    edited_text: str | None = None


@dataclass(frozen=True, slots=True)
class AnalysisRun:
    id: int
    version_id: int
    target_type: str
    target_id: int
    target_name: str
    rules_version: str
    created_at: str
    suggestions: tuple[Suggestion, ...] = field(default=())


@dataclass(frozen=True, slots=True)
class _Target:
    type: str
    id: int
    name: str
    terms: tuple[str, ...]
    last_checked_at: str | None


def analyse(
    connection: sqlite3.Connection,
    *,
    user_id: int,
    version_id: int,
    target_type: str,
    target_id: int,
    now: datetime | None = None,
) -> AnalysisRun:
    """Suggest how this CV version could fit one target; reuse an identical run."""

    if target_type not in ("faculty", "program"):
        raise CvError("target_type must be 'faculty' or 'program'.")
    checked_at = now or datetime.now(UTC)
    version = cv.get_version(connection, user_id=user_id, version_id=version_id)
    profile = profiles.load_profile(connection, user_id)
    target = _load_target(connection, target_type, target_id, checked_at)
    fingerprint = _fingerprint(version, target, profile)
    existing = connection.execute(
        "SELECT id FROM cv_analysis_runs WHERE user_id = ? AND version_id = ? "
        "AND target_type = ? AND target_id = ? AND inputs_fingerprint = ? "
        "ORDER BY id DESC LIMIT 1",
        (user_id, version_id, target_type, target_id, fingerprint),
    ).fetchone()
    if existing is not None:
        return get_run(connection, user_id=user_id, run_id=int(existing["id"]))

    drafts = _suggest(version.sections, target, profile)[:MAX_SUGGESTIONS]
    stamp = db.utc_now()
    with db.transaction(connection):
        cursor = connection.execute(
            "INSERT INTO cv_analysis_runs (user_id, version_id, target_type, target_id, "
            "rules_version, inputs_fingerprint, created_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (user_id, version_id, target_type, target_id, inputs_fingerprint) "
            "DO NOTHING",
            (user_id, version_id, target_type, target_id, RULES_VERSION, fingerprint, stamp),
        )
        if cursor.rowcount == 0:
            # Lost a race with an identical request (QA n3 on #45): reuse its run.
            row = connection.execute(
                "SELECT id FROM cv_analysis_runs WHERE user_id = ? AND version_id = ? "
                "AND target_type = ? AND target_id = ? AND inputs_fingerprint = ?",
                (user_id, version_id, target_type, target_id, fingerprint),
            ).fetchone()
            run_id = int(row["id"])
        else:
            run_id = int(cursor.lastrowid or 0)
            _insert_suggestions(connection, user_id, run_id, drafts, stamp)
            _carry_over_and_prune(
                connection, user_id, run_id, version_id, target_type, target_id, stamp
            )
        db.record_audit_event(
            connection,
            actor=f"user:{user_id}",
            action="cv.analysed",
            outcome="success",
            target_type="cv_version",
            target_id=str(version_id),
            metadata={
                "target": f"{target_type}:{target_id}",
                "suggestions": len(drafts),
                "rules_version": RULES_VERSION,
            },
        )
    return get_run(connection, user_id=user_id, run_id=run_id)


def _insert_suggestions(
    connection: sqlite3.Connection,
    user_id: int,
    run_id: int,
    drafts: Sequence[dict[str, object]],
    stamp: str,
) -> None:
    for position, draft in enumerate(drafts):
        connection.execute(
            "INSERT INTO cv_suggestions (run_id, user_id, position, kind, payload_json, "
            "updated_at) VALUES (?, ?, ?, ?, ?, ?)",
            (run_id, user_id, position, draft["kind"], json.dumps(draft), stamp),
        )


def _same_suggestion(a: dict[str, object], b: dict[str, object]) -> bool:
    keys = ("kind", "section", "bullet", "before", "after")
    return all(a.get(key) == b.get(key) for key in keys)


def _carry_over_and_prune(
    connection: sqlite3.Connection,
    user_id: int,
    run_id: int,
    version_id: int,
    target_type: str,
    target_id: int,
    stamp: str,
) -> None:
    """Keep the student's decisions on identical suggestions; drop older runs.

    A new run for the same version and target (profile or target changed) would
    otherwise reset every decision to pending (QA n2 on #45). Only identical
    suggestions inherit a decision; changed ones start pending.
    """

    older = [
        int(row["id"])
        for row in connection.execute(
            "SELECT id FROM cv_analysis_runs WHERE user_id = ? AND version_id = ? "
            "AND target_type = ? AND target_id = ? AND id <> ? ORDER BY id DESC",
            (user_id, version_id, target_type, target_id, run_id),
        )
    ]
    if not older:
        return
    decided = [
        (json.loads(row["payload_json"]), str(row["status"]), row["edited_text"])
        for row in connection.execute(
            "SELECT payload_json, status, edited_text FROM cv_suggestions "
            "WHERE run_id = ? AND user_id = ? AND status <> 'pending'",
            (older[0], user_id),
        )
    ]
    for row in connection.execute(
        "SELECT id, payload_json FROM cv_suggestions WHERE run_id = ? AND user_id = ?",
        (run_id, user_id),
    ).fetchall():
        payload = json.loads(row["payload_json"])
        for previous, status, edited in decided:
            if _same_suggestion(payload, previous):
                connection.execute(
                    "UPDATE cv_suggestions SET status = ?, edited_text = ?, updated_at = ? "
                    "WHERE id = ?",
                    (status, edited, stamp, int(row["id"])),
                )
                break
    placeholders = ", ".join("?" for _ in older)
    connection.execute(
        f"DELETE FROM cv_analysis_runs WHERE user_id = ? AND id IN ({placeholders})",  # noqa: S608
        (user_id, *older),
    )


def export_payload(connection: sqlite3.Connection, user_id: int) -> list[dict[str, object]]:
    """Every analysis run and its suggestions, for the account export (QA B1 on #45).

    Suggestions quote the student's own CV lines, so they are personal data.
    """

    runs: list[dict[str, object]] = []
    for row in connection.execute(
        "SELECT id FROM cv_analysis_runs WHERE user_id = ? ORDER BY id", (user_id,)
    ).fetchall():
        run = get_run(connection, user_id=user_id, run_id=int(row["id"]))
        runs.append(
            {
                "id": run.id,
                "version_id": run.version_id,
                "target_type": run.target_type,
                "target_id": run.target_id,
                "rules_version": run.rules_version,
                "created_at": run.created_at,
                "suggestions": [
                    {
                        "kind": item.kind,
                        "section": item.section,
                        "bullet": item.bullet,
                        "before": list(item.before),
                        "after": list(item.after),
                        "reason": item.reason,
                        "evidence": list(item.evidence),
                        "status": item.status,
                        "edited_text": item.edited_text,
                    }
                    for item in run.suggestions
                ],
            }
        )
    return runs


def get_run(connection: sqlite3.Connection, *, user_id: int, run_id: int) -> AnalysisRun:
    row = connection.execute(
        "SELECT * FROM cv_analysis_runs WHERE id = ? AND user_id = ?", (run_id, user_id)
    ).fetchone()
    if row is None:
        raise CvError(
            f"CV analysis {run_id} does not exist for this account.", code="cv_run_missing"
        )
    suggestions = tuple(
        _row_to_suggestion(item)
        for item in connection.execute(
            "SELECT * FROM cv_suggestions WHERE run_id = ? AND user_id = ? ORDER BY position",
            (run_id, user_id),
        )
    )
    name = _target_name(connection, str(row["target_type"]), int(row["target_id"]))
    return AnalysisRun(
        id=int(row["id"]),
        version_id=int(row["version_id"]),
        target_type=str(row["target_type"]),
        target_id=int(row["target_id"]),
        target_name=name,
        rules_version=str(row["rules_version"]),
        created_at=str(row["created_at"]),
        suggestions=suggestions,
    )


# --------------------------------------------------------------------------- #
# rules
# --------------------------------------------------------------------------- #


def _suggest(
    sections: Sequence[cv.CvSection], target: _Target, profile: profiles.StudentProfile
) -> list[dict[str, object]]:
    drafts: list[dict[str, object]] = []
    relevance = [_section_relevance(section, target.terms) for section in sections]

    # 1. Move the sections that match the target ahead of the ones that do not.
    order = list(range(len(sections)))
    movable = [i for i in order if sections[i].kind in MOVABLE_SECTIONS]
    ranked = sorted(movable, key=lambda i: (-relevance[i][0], i))
    if ranked != movable and relevance[ranked[0]][0] > 0:
        new_order = order[:]
        for slot, index in zip(movable, ranked, strict=True):
            new_order[slot] = index
        matched = sorted({term for i in ranked for term in relevance[i][1]})
        drafts.append(
            {
                "kind": "reorder_sections",
                "section": None,
                "bullet": None,
                "before": [_label(sections[i]) for i in order],
                "after": [_label(sections[i]) for i in new_order],
                "order": new_order,
                "reason": f"Put the sections closest to {target.name}'s work first.",
                "evidence": [f"{target.name}: {term}" for term in matched[:8]],
            }
        )

    # 2. Within a section, move matching bullets to the top (stable otherwise).
    for index, section in enumerate(sections):
        if section.kind not in MOVABLE_SECTIONS or len(section.bullets) < 2:
            continue
        scores = [_line_match(bullet, target.terms) for bullet in section.bullets]
        ranked_bullets = sorted(range(len(section.bullets)), key=lambda i: (-scores[i][0], i))
        if ranked_bullets != list(range(len(section.bullets))) and scores[ranked_bullets[0]][0]:
            matched = sorted({term for score in scores for term in score[1]})
            drafts.append(
                {
                    "kind": "reorder_bullets",
                    "section": index,
                    "bullet": None,
                    "before": list(section.bullets),
                    "after": [section.bullets[i] for i in ranked_bullets],
                    "order": ranked_bullets,
                    "reason": f"Lead '{_label(section)}' with the lines that match {target.name}.",
                    "evidence": [f"{target.name}: {term}" for term in matched[:8]],
                }
            )

    # 3. Confirmed profile items that match the target but are not in the CV.
    cv_text = " ".join(" ".join(_tokens(line)) for section in sections for line in section.bullets)
    skills_index = next((i for i, s in enumerate(sections) if s.kind == "skills"), None)
    for source, values in (
        ("skills", profile.skills),
        ("research keywords", profile.research_keywords),
        ("coursework", profile.coursework),
    ):
        for value in values:
            phrase = " ".join(_tokens(value))
            if not phrase or f" {phrase} " in f" {cv_text} ":
                continue
            credit, term = _phrase_credit(phrase, target.terms)
            if credit < 1.0:
                continue
            drafts.append(
                {
                    "kind": "surface_skill",
                    "section": skills_index,
                    "bullet": None,
                    "before": [],
                    "after": [value],
                    "reason": (
                        f"'{value}' is in your confirmed profile and matches {target.name}'s "
                        "work, but your CV does not mention it. Add it only if it is accurate."
                    ),
                    "evidence": [f"your profile ({source}): {value}", f"{target.name}: {term}"],
                }
            )

    # 4. Relevant bullets that state a duty but no result.
    for index, section in enumerate(sections):
        if section.kind not in ("experience", "projects"):
            continue
        for bullet_index, bullet in enumerate(section.bullets):
            score, terms = _line_match(bullet, target.terms)
            lowered = bullet.casefold()
            if not score or _HAS_RESULT.search(bullet):
                continue
            if not lowered.startswith(_WEAK_OPENERS) and len(bullet.split()) > 6:
                continue
            drafts.append(
                {
                    "kind": "flag_vague",
                    "section": index,
                    "bullet": bullet_index,
                    "before": [bullet],
                    "after": [bullet],
                    "reason": (
                        "This line matches the group's work but states no result. If you have "
                        "a concrete outcome (a number, a dataset, a publication), add it in your "
                        "own words; nothing is filled in for you."
                    ),
                    "evidence": [f"{target.name}: {term}" for term in terms[:4]],
                }
            )
    return drafts


def _line_match(line: str, terms: Sequence[str]) -> tuple[float, list[str]]:
    words = " ".join(_tokens(line))
    matched: list[str] = []
    total = 0.0
    for term in terms:
        credit, _ = _phrase_credit(term, (words,))
        if credit:
            total += credit
            matched.append(term)
    return total, matched


def _section_relevance(section: cv.CvSection, terms: Sequence[str]) -> tuple[float, list[str]]:
    """Distinct target terms a section matches.

    Counting distinct terms, not hits per line, keeps a long section from
    outranking a short, more relevant one by length alone (QA n1 on #45).
    """

    matched: set[str] = set()
    for bullet in section.bullets:
        matched.update(_line_match(bullet, terms)[1])
    return float(len(matched)), sorted(matched)


def _label(section: cv.CvSection) -> str:
    return section.heading or "Introduction"


# --------------------------------------------------------------------------- #
# targets and storage
# --------------------------------------------------------------------------- #


def _load_target(
    connection: sqlite3.Connection, target_type: str, target_id: int, now: datetime
) -> _Target:
    if target_type == "faculty":
        member = faculty_service.get_faculty(connection, target_id, now=now)
        terms = _phrases(
            (*member.research_interests, *(paper.title for paper in member.publications))
        )
        return _Target("faculty", member.id, member.full_name, terms, member.last_checked_at)
    program = discovery.get_program(connection, target_id, now=now)
    terms = _phrases((*program.research_areas, program.name))
    return _Target("program", program.id, program.name, terms, program.last_checked_at)


def _target_name(connection: sqlite3.Connection, target_type: str, target_id: int) -> str:
    table, column = (
        ("faculty_members", "full_name") if target_type == "faculty" else ("programs", "name")
    )
    row = connection.execute(
        f"SELECT {column} AS name FROM {table} WHERE id = ?",  # noqa: S608 - fixed identifiers
        (target_id,),
    ).fetchone()
    return "a record no longer in the cache" if row is None else str(row["name"])


def _fingerprint(version: cv.CvVersion, target: _Target, profile: profiles.StudentProfile) -> str:
    payload = {
        "rules": RULES_VERSION,
        "version": version.content_sha256,
        "target": [target.type, target.id, target.last_checked_at],
        "profile": [
            list(profile.skills),
            list(profile.research_keywords),
            list(profile.coursework),
        ],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode("utf-8")).hexdigest()


def _row_to_suggestion(row: sqlite3.Row) -> Suggestion:
    payload = json.loads(row["payload_json"])
    return Suggestion(
        id=int(row["id"]),
        position=int(row["position"]),
        kind=str(row["kind"]),
        section=payload.get("section"),
        bullet=payload.get("bullet"),
        before=tuple(payload.get("before", ())),
        after=tuple(payload.get("after", ())),
        reason=str(payload.get("reason", "")),
        evidence=tuple(payload.get("evidence", ())),
        status=str(row["status"]),
        edited_text=None if row["edited_text"] is None else str(row["edited_text"]),
    )


# --------------------------------------------------------------------------- #
# decisions and approval (Phase 7c)
# --------------------------------------------------------------------------- #

DECISIONS = frozenset({"accepted", "rejected", "edited", "pending"})
#: Kinds whose text the student may replace with their own words.
EDITABLE_KINDS = frozenset({"surface_skill", "flag_vague"})
MAX_EDIT_LENGTH = 500


def decide(
    connection: sqlite3.Connection,
    *,
    user_id: int,
    suggestion_id: int,
    status: str,
    edited_text: str | None = None,
) -> Suggestion:
    """Record the student's decision on one suggestion. Nothing is applied yet."""

    if status not in DECISIONS:
        raise CvError("status must be one of accepted, rejected, edited or pending.")
    row = connection.execute(
        "SELECT * FROM cv_suggestions WHERE id = ? AND user_id = ?", (suggestion_id, user_id)
    ).fetchone()
    if row is None:
        raise CvError(
            f"suggestion {suggestion_id} does not exist for this account.",
            code="cv_suggestion_missing",
        )
    text: str | None = None
    if status == "edited":
        if str(row["kind"]) not in EDITABLE_KINDS:
            raise CvError(
                "only a skill or a flagged line can be edited; accept or reject a reorder."
            )
        text = " ".join((edited_text or "").split())
        if not text:
            raise CvError("edited_text is required when status is 'edited'.")
        if len(text) > MAX_EDIT_LENGTH:
            raise CvError(f"edited_text must be at most {MAX_EDIT_LENGTH} characters.")
    elif edited_text is not None:
        raise CvError("edited_text is only allowed when status is 'edited'.")
    connection.execute(
        "UPDATE cv_suggestions SET status = ?, edited_text = ?, updated_at = ? WHERE id = ?",
        (status, text, db.utc_now(), suggestion_id),
    )
    return _row_to_suggestion(
        connection.execute("SELECT * FROM cv_suggestions WHERE id = ?", (suggestion_id,)).fetchone()
    )


def preview(
    connection: sqlite3.Connection,
    *,
    user_id: int,
    run_id: int,
    run: AnalysisRun | None = None,
) -> tuple[cv.CvSection, ...]:
    """The tailored CV the accepted and edited suggestions would produce.

    Pending and rejected suggestions are never applied: approval must not assume
    a suggestion the student has not accepted (§3.7).
    """

    if run is None or run.id != run_id:
        run = get_run(connection, user_id=user_id, run_id=run_id)
    version = cv.get_version(connection, user_id=user_id, version_id=run.version_id)
    rows = {
        int(row["id"]): json.loads(row["payload_json"])
        for row in connection.execute(
            "SELECT id, payload_json FROM cv_suggestions WHERE run_id = ?", (run_id,)
        )
    }
    return _apply(version.sections, run.suggestions, rows)


def approve(connection: sqlite3.Connection, *, user_id: int, run_id: int) -> cv.CvVersion:
    """Create the professor-specific version from accepted changes, on explicit request."""

    run = get_run(connection, user_id=user_id, run_id=run_id)
    applied = [s for s in run.suggestions if s.status in ("accepted", "edited")]
    if not applied:
        raise CvError(
            "accept or edit at least one suggestion before approving a tailored CV.",
            code="cv_nothing_accepted",
        )
    sections = preview(connection, user_id=user_id, run_id=run_id, run=run)
    digest = cv.content_hash(sections)
    parent = cv.get_version(connection, user_id=user_id, version_id=run.version_id)
    if digest == parent.content_sha256:
        # An accepted flag with no edit changes nothing; a copy identical to its
        # parent would only add noise (QA n1 on #46).
        raise CvError(
            "the accepted suggestions do not change the CV; edit a flagged line or accept "
            "a reorder or skill before approving.",
            code="cv_nothing_changed",
        )
    existing = connection.execute(
        "SELECT id FROM cv_versions WHERE user_id = ? AND parent_version_id = ? "
        "AND target_type = ? AND target_id = ? AND content_sha256 = ?",
        (user_id, run.version_id, run.target_type, run.target_id, digest),
    ).fetchone()
    if existing is not None:
        return cv.get_version(connection, user_id=user_id, version_id=int(existing["id"]))
    version = cv.get_version(connection, user_id=user_id, version_id=run.version_id)
    stamp = db.utc_now()
    with db.transaction(connection):
        cursor = connection.execute(
            "INSERT INTO cv_versions (user_id, document_id, kind, parent_version_id, "
            "target_type, target_id, sections_json, content_sha256, created_at, approved_at) "
            "VALUES (?, ?, 'tailored', ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (parent_version_id, target_type, target_id, content_sha256) "
            "WHERE kind = 'tailored' DO NOTHING",
            (
                user_id,
                version.document_id,
                run.version_id,
                run.target_type,
                run.target_id,
                json.dumps(cv.sections_payload(sections), ensure_ascii=False),
                digest,
                stamp,
                stamp,
            ),
        )
        if cursor.rowcount == 0:
            # A concurrent identical approval won; return its version.
            row = connection.execute(
                "SELECT id FROM cv_versions WHERE user_id = ? AND parent_version_id = ? "
                "AND target_type = ? AND target_id = ? AND content_sha256 = ?",
                (user_id, run.version_id, run.target_type, run.target_id, digest),
            ).fetchone()
            return cv.get_version(connection, user_id=user_id, version_id=int(row["id"]))
        new_id = int(cursor.lastrowid or 0)
        db.record_audit_event(
            connection,
            actor=f"user:{user_id}",
            action="cv.version_approved",
            outcome="success",
            target_type="cv_version",
            target_id=str(new_id),
            metadata={
                "parent_version_id": run.version_id,
                "target": f"{run.target_type}:{run.target_id}",
                "applied": len(applied),
            },
        )
    return cv.get_version(connection, user_id=user_id, version_id=new_id)


def diff(before: Sequence[cv.CvSection], after: Sequence[cv.CvSection]) -> list[dict[str, object]]:
    """A line diff between two versions, section headings included, for display."""

    import difflib

    def lines(sections: Sequence[cv.CvSection]) -> list[str]:
        out: list[str] = []
        for section in sections:
            out.append(f"## {_label(section)}")
            out.extend(section.bullets)
        return out

    old, new = lines(before), lines(after)
    changes: list[dict[str, object]] = []
    for tag, i1, i2, j1, j2 in difflib.SequenceMatcher(a=old, b=new, autojunk=False).get_opcodes():
        if tag == "equal":
            changes.extend({"op": "same", "line": line} for line in old[i1:i2])
            continue
        changes.extend({"op": "removed", "line": line} for line in old[i1:i2])
        changes.extend({"op": "added", "line": line} for line in new[j1:j2])
    return changes


def _apply(
    sections: Sequence[cv.CvSection],
    suggestions: Sequence[Suggestion],
    payloads: dict[int, dict[str, object]],
) -> tuple[cv.CvSection, ...]:
    """Apply accepted/edited suggestions to the parent version, in a fixed order.

    Line edits first, then bullet reorders, then surfaced skills, then the
    section reorder, so every index still refers to the parent version.
    """

    bullets = [list(section.bullets) for section in sections]
    chosen = [s for s in suggestions if s.status in ("accepted", "edited")]
    for item in chosen:
        if (
            item.kind == "flag_vague"
            and item.status == "edited"
            and item.edited_text
            and item.section is not None
            and item.bullet is not None
        ):
            bullets[item.section][item.bullet] = item.edited_text
    for item in chosen:
        if item.kind == "reorder_bullets" and item.section is not None:
            order = [int(i) for i in payloads[item.id].get("order", [])]
            current = bullets[item.section]
            if sorted(order) == list(range(len(current))):
                bullets[item.section] = [current[i] for i in order]
    added: list[str] = []
    for item in chosen:
        if item.kind == "surface_skill":
            text = (
                item.edited_text if item.status == "edited" and item.edited_text else item.after[0]
            )
            added.append(text)
    result = [
        cv.CvSection(kind=section.kind, heading=section.heading, bullets=tuple(lines))
        for section, lines in zip(sections, bullets, strict=True)
    ]
    if added:
        index = next((i for i, s in enumerate(result) if s.kind == "skills"), None)
        if index is None:
            result.append(cv.CvSection(kind="skills", heading="Skills", bullets=tuple(added)))
        else:
            skills = result[index]
            result[index] = cv.CvSection(
                kind=skills.kind, heading=skills.heading, bullets=(*skills.bullets, *added)
            )
    for item in chosen:
        if item.kind == "reorder_sections":
            order = [int(i) for i in payloads[item.id].get("order", [])]
            if sorted(order) == list(range(len(sections))):
                tail = result[len(sections) :]
                result = [result[i] for i in order] + list(tail)
    return tuple(result)
