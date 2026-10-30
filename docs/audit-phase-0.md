# Phase 0 — Repository and environment audit

Date: 2026-10-30
Author: Dev agent
Scope: read-only audit required by `task.md` §11. No existing file was
overwritten, no database reset, no force-push, no infrastructure replaced.

## 1. What already exists

The repository contains a scaffold only (8 tracked files, one commit
`7ed17e1 chore: scaffold the Python project and its dev/QA loop`):

| Path | Purpose |
| --- | --- |
| `pyproject.toml` | Project metadata, Ruff config, pytest config |
| `src/research_inter_bridge/__init__.py` | `__version__` only |
| `tests/test_smoke.py` | One assertion on `__version__` |
| `.github/workflows/ci.yml` | `lint` job + `test` matrix (3 OS) |
| `.github/pull_request_template.md` | What/Why/Verification/Risk |
| `AGENTS.md`, `README.md`, `.gitignore` | Team rules, stub README |

There is **no** application code, no database layer, no API, no frontend, no
migrations, no document parsing, no research adapters, no OAuth, no MCP server,
no scheduler, no `docs/` directory and no `.env.example`.

## 2. What is missing relative to the specification

All twelve product areas are unimplemented: onboarding and profile,
document ingestion and confirmed facts, program discovery, faculty/publication
discovery, matching, shortlist, draft generation, Gmail/Outlook OAuth, MCP
servers, approval-gated sending, scheduling cap/blackouts, reply monitoring and
follow-ups, dashboard and notifications. Sections 4–7 of this report map the
work to the phases in `task.md` §7.

## 3. Reusable components

* `pyproject.toml` (Ruff `E,F,I,UP,B,SIM`, pytest `pythonpath=src`) — kept.
* `.github/workflows/ci.yml` — kept; only the dependency-install step will need
  `pip install -e .` once third-party dependencies exist.
* PR template and `AGENTS.md` loop — kept.
* `src/` layout and `tests/` layout — kept.
* `agent-bridge` (daemon + `dev`/`qa` sessions) — reused for handoffs.

## 4. Actual toolchain and commands

```text
$ python --version
Python 3.10.11                # C:\Users\shash\AppData\Local\Programs\Python\Python310
$ python -m ruff --version
ruff 0.17.3
$ python -m pytest --version
pytest 8.4.1
```

Also installed on this machine: Python 3.11.9 and 3.13.4.
`pyproject.toml` declares `requires-python = ">=3.11"` and Ruff
`target-version = "py311"`; CI pins Python 3.12.

Working commands (run from the repository root, no virtualenv required because
pytest uses `pythonpath = ["src"]`):

```text
$ python -m ruff check .              -> All checks passed!                    (exit 0)
$ python -m ruff format --check .     -> 17 files already formatted            (exit 0)
$ python -m pytest -q                 -> 42 passed                             (exit 0, Python 3.10.11)
$ python -m pytest -q                 -> 42 passed                             (exit 0, Python 3.11.9)
$ PYTHONPATH=src python -m research_inter_bridge migrate
$ PYTHONPATH=src python -m research_inter_bridge health
```

## 5. CI reality

`gh run list` shows `CI` green for `main` and `dev` on commit `7ed17e1`
(`lint`, plus `test (ubuntu-latest)`, `test (windows-latest)`,
`test (macos-latest)`, 23–24 s each).

Branch protection on `main` (`gh api .../branches/main/protection`):

* required checks: `lint`, `test (ubuntu-latest)`, `test (windows-latest)`,
  `test (macos-latest)` — exactly four, as the Owner stated;
* `strict: true`, `enforce_admins: true`;
* `required_pull_request_reviews: null` — reviews are a *team rule* from
  `AGENTS.md`, not a GitHub-enforced setting. Do not assume GitHub blocks a
  self-merge; only the Owner rule does.

## 6. agent-bridge reality

```text
$ agent-bridge ping        -> pong
$ agent-bridge version     -> protocol 1, daemon 0.1.2, pid 8172
$ agent-bridge listagents  -> dev (running, pid 26152, 1 msg), qa (running, pid 19544)
```

Worktrees: `.../ResearchInterBridge` (branch `main`, later moved to feature
branches) and `.../ResearchInterBridge-qa` (branch `qa-review`).
Available subcommands: `daemon`, `listagents`, `sendmessage`, `new`, `read`,
`kill`, `attach`, `log`, `ping`, `version`. No `tasks`/`assign` command exists,
so handoffs are plain messages plus PR comments.

## 7. Conflicts found — resolved 2026-10-30

Decisions are recorded in `docs/decisions.md`; this section keeps the original
finding so the history stays auditable.

1. **Python version — RESOLVED (ADR-001).** `task.md` §0 says 3.10.x;
   `pyproject.toml`, `AGENTS.md` §5 and CI say 3.11+/3.12. Floor is **3.11**;
   `task.md` §0 is superseded, `datetime.UTC` is now used (the `# noqa: UP017`
   workaround is gone) and CI gained an additive `test-python311` job.
   Local runs use `py -3.11` because the machine default is 3.10.11.
2. **CI dependency install — OPEN, by design.** CI installs only `pytest` today
   because there is no runtime dependency. The first third-party dependency must
   add `pip install -e .` to every job while keeping the four required check
   names unchanged.
3. **Quality gates — CORRECTION.** `AGENTS.md` §4 *does* already require
   `ruff format --check .`; my original audit note was wrong. No change needed.
4. **Frontend stack — RESOLVED (ADR-002).** Backend-first until Phase 10.
5. **`task.md` visibility — RESOLVED (ADR-003).** The specification is committed
   so the QA worktree can read it.
6. **Gmail/Outlook credentials — BLOCKED (ADR-006).** No Google Cloud project,
   OAuth client or Workspace policy answer yet. Phase 7 ships mocked adapters
   first; live connect needs `RIB_GOOGLE_OAUTH_CLIENT_ID`/`_SECRET` in `.env`
   supplied by the Owner. No secret will be committed, logged or sent over the
   bridge.

## 8. Prioritised plan and current progress

| Phase | Slice | Status |
| --- | --- | --- |
| 0 | Audit report + toolchain verification | **done — this document** |
| 1 | Config from env, redacted logging, SQLite + forward-only migrations, audit events, health CLI, `.env.example` | **done — PR `feat/phase-1-foundation`** |
| 2 | Profile model, onboarding, document upload/parse and confirmed facts | next |
| 3 | Program discovery adapters + provenance (+ offline fixtures) | planned |
| 4 | Faculty/publication discovery, email verification rules | planned |
| 5 | Matching score with hard eligibility gates | planned |
| 6 | Evidence-backed drafts and versioning | planned |
| 7 | OAuth + provider adapters + MCP servers (mocked tests first) | planned |
| 8 | Approval + scheduler (timezone, DST, December, 5/university/day) | planned |
| 9 | Reply detection and three-working-day follow-ups | planned |
| 10 | Dashboard and notifications | planned |
| 11 | Security/reliability review and release evidence | planned |

First small task (already delivered in this PR): the foundation slice above.
Verification evidence lives in the PR body, and every rule that the specification
calls security-critical was deliberately kept out of this slice until its policy
service exists.

## 9. Known limitations of the current increment

* No HTTP API yet (`health`/`migrate` are CLI only) — Phase 1 continuation.
* Single SQLite file, no PostgreSQL adapter; no backup/restore script yet.
* Only one migration exists (`audit_events`); product tables arrive with the
  services that own them.
* Nothing sends email, calls a scholarly API or touches OAuth, so no
  approval/policy bypass is possible yet.

## 10. QA follow-up status (PR #1 review, `fix/phase-1-qa-findings`)

PR #1 was merged before a verdict was posted, so the review arrived as
fix-forward. Status of each finding:

| Finding | Status |
| --- | --- |
| M1 tracebacks bypass redaction | **fixed** — `RedactingFormatter` redacts the final formatted output, incl. `exc_info`; matrix tests cover the exception channel |
| M2 `Authorization: Basic <base64>` leaks | **fixed** — auth-scheme value rule + scheme pattern; Basic/Digest/Bearer tested |
| M3 JSON / dict-repr keys bypass | **fixed** — keys may be quoted; test matrix covers JSON, indented JSON and dict repr |
| M4 audit event commits caller transaction | **fixed** — `record_audit_event` no longer commits; rollback removes both rows |
| m1 `migrate` raw traceback on bad path | **fixed** — catches `sqlite3.Error`, prints `migration failed: ...`, exit 1 |
| m2 dead `RIB_REDACT_LOGS` knob | **fixed** — env var removed; `redact_logs` is a non-init constant |
| n1 per-handler filter is bypassable | **fixed** — redacting formatter installed by `configure_logging` |
| m3 health check mutates the DB | **fixed** — `db.connect_readonly` (`mode=ro`) + `db.schema_version`; regression test asserts no tables are created |
| m4 unquoted multi-word secret partially masked | documented limitation in `logging.py`; quoted values are redacted fully |
| n2 absolute DB path in health JSON | deferred; do not reuse the payload for an HTTP probe |
| n3 CI tests only 3.12 | **fixed** — additive `test-python311` job (required check names unchanged) |
