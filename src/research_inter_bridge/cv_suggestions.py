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
            "rules_version, inputs_fingerprint, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (user_id, version_id, target_type, target_id, RULES_VERSION, fingerprint, stamp),
        )
        run_id = int(cursor.lastrowid or 0)
        for position, draft in enumerate(drafts):
            connection.execute(
                "INSERT INTO cv_suggestions (run_id, user_id, position, kind, payload_json, "
                "updated_at) VALUES (?, ?, ?, ?, ?, ?)",
                (run_id, user_id, position, draft["kind"], json.dumps(draft), stamp),
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
    total = 0.0
    matched: list[str] = []
    for bullet in section.bullets:
        score, found = _line_match(bullet, terms)
        total += score
        matched.extend(found)
    return total, matched


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
