"""Deterministic draft lint before approval (task.md §14.2, ADR-026).

Pure functions: no database, no network, no clock. One pass over the subject and
body (each regex runs once over the text), so a 10 KB body lints in linear
time. Every finding has a stable ``code``, a level and a message that says
exactly what to change.

* ``block``: a §14.2 rule. Approval is refused until it is fixed.
* ``warn``: a style issue the student may keep.

Spam phrases, URL shorteners and redirect hosts are plain data lists below, so
they can be reviewed and extended without touching the rules.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal
from urllib.parse import parse_qs, urlsplit

Level = Literal["block", "warn"]

# --------------------------------------------------------------------------- #
# data lists
# --------------------------------------------------------------------------- #

SPAM_PHRASES: tuple[str, ...] = (
    "act now",
    "limited time",
    "urgent response",
    "respond immediately",
    "100% guaranteed",
    "guaranteed",
    "risk free",
    "risk-free",
    "free money",
    "earn money",
    "cash prize",
    "you have been selected",
    "congratulations you",
    "click here",
    "buy now",
    "special offer",
    "once in a lifetime",
    "no obligation",
    "winner",
    "dear friend",
)

SHORTENER_HOSTS: frozenset[str] = frozenset(
    {
        "bit.ly",
        "tinyurl.com",
        "t.co",
        "goo.gl",
        "ow.ly",
        "is.gd",
        "buff.ly",
        "rebrand.ly",
        "cutt.ly",
        "shorturl.at",
        "rb.gy",
        "tiny.cc",
        "lnkd.in",
    }
)

REDIRECT_HOSTS: frozenset[str] = frozenset(
    {"l.facebook.com", "lm.facebook.com", "out.reddit.com", "href.li", "l.instagram.com"}
)
REDIRECT_PARAMS: frozenset[str] = frozenset(
    {"url", "u", "redirect", "redirect_uri", "target", "dest", "q"}
)
#: Google's redirector puts the target in ``q`` or ``url`` under ``/url``.
REDIRECT_PATHS: frozenset[str] = frozenset({"/url", "/redirect", "/out"})

ALLOWED_ATTACHMENT_KINDS: frozenset[str] = frozenset({"cv"})
MAX_LINKS = 2
MAX_ATTACHMENTS = 1
MAX_SUBJECT_LENGTH = 78
MAX_EXCLAMATIONS = 1
MAX_BODY_WORDS = 350
#: Share of shouted words (3+ letters, all caps) that reads as shouting.
MAX_CAPS_SHARE = 0.3

_LINK_RE = re.compile(r"\b(?:https?://|www\.)[^\s<>()\"']+", re.I)
_HTML_RE = re.compile(r"<\s*/?\s*[a-z][a-z0-9]*[\s>/]", re.I)
_REPLY_RE = re.compile(r"^\s*(?:re|fw|fwd)\s*:", re.I)
_SPAM_RE = re.compile(
    r"(?<!\w)(?:"
    + "|".join(re.escape(p) for p in sorted(SPAM_PHRASES, key=len, reverse=True))
    + r")(?!\w)",
    re.I,
)
_WORD_RE = re.compile(r"[A-Za-z]{3,}")
_NOREPLY_RE = re.compile(r"no[-_.]?reply|do[-_.]?not[-_.]?reply", re.I)


@dataclass(frozen=True, slots=True)
class Finding:
    code: str
    level: Level
    message: str


@dataclass(frozen=True, slots=True)
class LintInput:
    subject: str
    body: str
    recipient: str | None
    #: Kinds of the attached files ("cv"); at most one is allowed.
    attachment_kinds: Sequence[str] = ()
    #: Drafts of the same student whose body is identical (normalised).
    duplicate_draft_ids: Sequence[int] = ()
    #: Verified paper titles or research topics the body must still mention.
    references: Sequence[str] = ()
    #: Sender display name and address, once an account is connected (Phase 9).
    sender_name: str | None = None
    sender_address: str | None = None


def lint(draft: LintInput) -> tuple[Finding, ...]:
    """All findings, blocking ones first, in a stable order."""

    findings: list[Finding] = []
    subject = draft.subject.strip()
    body = draft.body.strip()

    def block(code: str, message: str) -> None:
        findings.append(Finding(code, "block", message))

    def warn(code: str, message: str) -> None:
        findings.append(Finding(code, "warn", message))

    # --- identity and content presence -----------------------------------
    if not subject:
        block("subject_empty", "Add a subject line.")
    if not body:
        block("body_empty", "Write the email body.")
    if draft.recipient is None:
        block(
            "recipient_unverified",
            "This professor has no verified, current email address; the draft cannot be "
            "approved until one is verified on their official page.",
        )
    if draft.sender_address is not None and _NOREPLY_RE.search(draft.sender_address):
        block("sender_noreply", "Send from your own address, not a no-reply address.")
    if draft.sender_name is not None and not draft.sender_name.strip():
        block("sender_name_missing", "Set a real display name for the sending account.")

    # --- subject ------------------------------------------------------------
    if subject:
        if _REPLY_RE.match(subject):
            block("subject_reply_prefix", "Remove 'RE:' or 'FWD:' — this is a first contact.")
        letters = [ch for ch in subject if ch.isalpha()]
        if len(letters) >= 4 and all(ch.isupper() for ch in letters):
            block("subject_all_caps", "Write the subject in normal case, not all capitals.")
        if len(subject) > MAX_SUBJECT_LENGTH:
            warn(
                "subject_long",
                f"Shorten the subject to {MAX_SUBJECT_LENGTH} characters or fewer so it is "
                "not cut off.",
            )

    # --- body: HTML, links, spam -------------------------------------------
    text = f"{subject}\n{body}"
    if _HTML_RE.search(text):
        block(
            "html_not_allowed",
            "Remove HTML tags: drafts are plain text, so images, tracking pixels and hidden "
            "text are not allowed.",
        )
    links = _LINK_RE.findall(body)
    if len(links) > MAX_LINKS:
        block(
            "too_many_links",
            f"Keep at most {MAX_LINKS} links (the paper and/or the official profile); "
            f"this draft has {len(links)}.",
        )
    for link in links:
        host, path, query = _parts(link)
        if host in SHORTENER_HOSTS:
            block("url_shortener", f"Replace the shortened link ({host}) with the full address.")
        elif host in REDIRECT_HOSTS or (path in REDIRECT_PATHS and REDIRECT_PARAMS & set(query)):
            block("redirect_link", f"Link directly to the page instead of through {host}.")
    spam = sorted({match.group(0).casefold() for match in _SPAM_RE.finditer(text)})
    if spam:
        block(
            "spam_phrase",
            "Remove wording that reads as spam: " + ", ".join(f"'{p}'" for p in spam) + ".",
        )

    # --- uniqueness and reference -------------------------------------------
    if draft.duplicate_draft_ids:
        block(
            "duplicate_body",
            "This body is identical to another of your drafts; personalise it for this professor.",
        )
    if draft.references:
        lowered = body.casefold()
        if not any(reference.casefold() in lowered for reference in draft.references):
            block(
                "no_specific_reference",
                "Mention this professor's specific paper or research topic: "
                + " or ".join(f"'{r}'" for r in draft.references[:2])
                + ".",
            )
    else:
        block(
            "no_specific_reference",
            "Add a specific, verified paper or research topic of this professor before approving.",
        )

    # --- attachments ----------------------------------------------------------
    if len(draft.attachment_kinds) > MAX_ATTACHMENTS:
        block("too_many_attachments", "Attach at most one file: your CV.")
    for kind in draft.attachment_kinds:
        if kind not in ALLOWED_ATTACHMENT_KINDS:
            block("attachment_type", "Only your CV can be attached.")

    # --- style warnings -------------------------------------------------------
    exclamations = text.count("!")
    if exclamations > MAX_EXCLAMATIONS:
        warn("exclamations", f"Use fewer exclamation marks ({exclamations} found).")
    words = _WORD_RE.findall(body)
    if words:
        shouted = sum(1 for word in words if word.isupper())
        if shouted / len(words) > MAX_CAPS_SHARE and shouted >= 3:
            warn("excessive_caps", "Write in normal case; many words are in capitals.")
    if len(body.split()) > MAX_BODY_WORDS:
        warn("body_long", f"Keep the email under about {MAX_BODY_WORDS} words; busy readers skim.")

    return tuple(sorted(findings, key=lambda f: (f.level != "block",)))


def blocking(findings: Sequence[Finding]) -> tuple[Finding, ...]:
    return tuple(finding for finding in findings if finding.level == "block")


def _parts(link: str) -> tuple[str, str, list[str]]:
    url = link if "://" in link else f"http://{link}"
    try:
        parts = urlsplit(url)
    except ValueError:
        return "", "", []
    host = (parts.hostname or "").casefold().removeprefix("www.")
    return host, parts.path or "/", list(parse_qs(parts.query))
