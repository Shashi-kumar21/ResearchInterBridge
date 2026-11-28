"""§14.2 draft lint (Phase 8b, ADR-026): pure, deterministic, one pass."""

from __future__ import annotations

import socket
import time

import pytest

from research_inter_bridge import lint

GOOD_BODY = (
    'Dear Professor Jane Doe,\n\nYour paper "Protein Folding with Deep Learning" caught my '
    "interest. It connects with my work on protein models.\n\nWould you consider me for a "
    "research internship? My CV is attached.\n\nKind regards,\nAsha Rao\nasha@example.edu\n"
    "https://doi.org/10.1234/pf"
)


def draft(**overrides: object) -> lint.LintInput:
    values: dict[str, object] = {
        "subject": "Research internship inquiry: protein folding",
        "body": GOOD_BODY,
        "recipient": "jane.doe@cs.example.edu",
        "attachment_kinds": ("cv",),
        "references": ("Protein Folding with Deep Learning",),
    }
    values.update(overrides)
    return lint.LintInput(**values)  # type: ignore[arg-type]


def codes(findings: tuple[lint.Finding, ...], level: str | None = None) -> set[str]:
    return {f.code for f in findings if level is None or f.level == level}


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*args: object, **kwargs: object) -> None:
        raise AssertionError("lint must not call out")

    monkeypatch.setattr(socket.socket, "connect", refuse)


def test_a_clean_draft_has_no_findings() -> None:
    assert lint.lint(draft()) == ()


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        ({"subject": ""}, "subject_empty"),
        ({"body": "   "}, "body_empty"),
        ({"recipient": None}, "recipient_unverified"),
        ({"sender_address": "no-reply@uni.edu"}, "sender_noreply"),
        ({"sender_name": "  "}, "sender_name_missing"),
        ({"subject": "RE: internship"}, "subject_reply_prefix"),
        ({"subject": "FWD: internship"}, "subject_reply_prefix"),
        ({"subject": "RESEARCH INTERNSHIP"}, "subject_all_caps"),
        (
            {"body": GOOD_BODY + '\n<img src="https://t.example/p.gif" width="1">'},
            "html_not_allowed",
        ),
        ({"body": GOOD_BODY + "\nhttps://a.edu https://b.edu"}, "too_many_links"),
        (
            {"body": GOOD_BODY.replace("https://doi.org/10.1234/pf", "https://bit.ly/x1")},
            "url_shortener",
        ),
        (
            {
                "body": GOOD_BODY.replace(
                    "https://doi.org/10.1234/pf", "https://www.google.com/url?q=https://x.edu"
                )
            },
            "redirect_link",
        ),
        ({"body": GOOD_BODY + "\nThis is a limited time chance, act now."}, "spam_phrase"),
        ({"duplicate_draft_ids": (7,)}, "duplicate_body"),
        (
            {"body": GOOD_BODY.replace("Protein Folding with Deep Learning", "your work")},
            "no_specific_reference",
        ),
        ({"references": ()}, "no_specific_reference"),
        ({"attachment_kinds": ("cv", "cv")}, "too_many_attachments"),
        ({"attachment_kinds": ("transcript",)}, "attachment_type"),
    ],
)
def test_each_spec_rule_blocks_with_a_stable_code(overrides: dict[str, object], code: str) -> None:
    findings = lint.lint(draft(**overrides))

    assert code in codes(findings, "block")
    assert all(f.message.endswith((".", ")")) for f in findings)


@pytest.mark.parametrize(
    ("overrides", "code"),
    [
        (
            {
                "subject": "Research internship inquiry about protein folding and deep "
                "learning methods 2027"
            },
            "subject_long",
        ),
        ({"body": GOOD_BODY + "\nThank you!!"}, "exclamations"),
        (
            # Capitals must be a large share of the words, not one shouted line.
            {"body": "Protein Folding with Deep Learning PLEASE READ THIS VERY IMPORTANT NOTE NOW"},
            "excessive_caps",
        ),
        ({"body": GOOD_BODY + "\n" + "word " * 400}, "body_long"),
    ],
)
def test_style_issues_are_only_warnings(overrides: dict[str, object], code: str) -> None:
    findings = lint.lint(draft(**overrides))

    assert code in codes(findings, "warn")
    assert lint.blocking(findings) == ()


def test_blocking_findings_come_first_and_messages_say_what_to_fix() -> None:
    findings = lint.lint(draft(subject="RE: hi!!", body=GOOD_BODY + "\nclick here!!"))

    levels = [f.level for f in findings]
    assert levels == sorted(levels, key=lambda level: level != "block")
    spam = next(f for f in findings if f.code == "spam_phrase")
    assert "'click here'" in spam.message


def test_spam_phrases_need_word_boundaries() -> None:
    # "guaranteed" inside a longer word must not trip the list.
    body = GOOD_BODY + "\nI study unguaranteedness in proofs and the winners' curse."
    assert "spam_phrase" not in codes(lint.lint(draft(body=body)))


def test_the_lists_are_data() -> None:
    assert "bit.ly" in lint.SHORTENER_HOSTS
    assert "limited time" in lint.SPAM_PHRASES
    assert {"cv"} == lint.ALLOWED_ATTACHMENT_KINDS


def test_a_10kb_body_lints_quickly_and_deterministically() -> None:
    body = (GOOD_BODY + "\n") + ("Plain sentence about research and methods. " * 250)
    assert len(body) > 10_000

    start = time.perf_counter()
    first = lint.lint(draft(body=body))
    elapsed = time.perf_counter() - start

    assert elapsed < 0.2
    assert lint.lint(draft(body=body)) == first


def test_a_verified_title_with_a_spam_word_never_blocks() -> None:
    # QA B1 on #52: the body must quote the title, so it cannot be spam.
    title = "Provably Guaranteed Convergence of Federated Learning"
    body = GOOD_BODY.replace("Protein Folding with Deep Learning", title)

    findings = lint.lint(draft(body=body, references=(title,)))

    assert "spam_phrase" not in codes(findings)
    # The same word written by the student elsewhere is still caught.
    extra = lint.lint(draft(body=body + "\nSuccess is guaranteed.", references=(title,)))
    assert "spam_phrase" in codes(extra, "block")


def test_shortener_and_redirect_subdomains_and_subject_links_are_caught() -> None:
    # QA n2 and n3 on #52.
    sub = GOOD_BODY.replace("https://doi.org/10.1234/pf", "https://m.bit.ly/x")
    assert "url_shortener" in codes(lint.lint(draft(body=sub)), "block")
    assert "url_shortener" in codes(lint.lint(draft(subject="See https://tinyurl.com/x")), "block")
    many = lint.lint(draft(subject="https://a.edu https://b.edu"))
    assert "too_many_links" in codes(many, "block")


def test_a_shortened_title_in_the_subject_is_not_spam() -> None:
    title = "Provably Guaranteed Convergence of Federated Learning Under Heavy Tailed Noise Models"
    body = GOOD_BODY.replace("Protein Folding with Deep Learning", title)
    subject = "Research internship inquiry: Provably Guaranteed Convergence of Federated…"

    assert "spam_phrase" not in codes(
        lint.lint(draft(subject=subject, body=body, references=(title,)))
    )
