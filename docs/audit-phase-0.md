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

## 7. Conflicts found (need an Owner decision)

1. **Python version.** `task.md` §0 states "Python compatibility: Python
   3.10.x", while `pyproject.toml` and `AGENTS.md` require `>=3.11` and CI runs
   3.12, and the machine's default `python` is 3.10.11. The foundation slice is
   written to run on both 3.10 and 3.11+ (the suite passes on both), and one
   `# noqa: UP017` documents the `datetime.UTC` (3.11-only) avoidance. Choose
   one and I will align `requires-python`, the Ruff `target-version` and CI.
2. **CI dependency install.** CI currently installs only `pytest`; any future
   third-party dependency requires adding `pip install -e .` to both jobs so
   the four required check names stay unchanged.
3. **Quality gates beyond `AGENTS.md`.** `AGENTS.md` §4 does not require
   `ruff format`; `task.md` §8 does not mention it either, but CI enforces
   `ruff format --check .`. The DoD in `AGENTS.md` should list it (it already
   is listed in my checklist below).
4. **Frontend stack.** `task.md` §3 suggests Next.js + TypeScript if no UI
   exists. Nothing exists yet; a backend-first approach keeps CI offline and
   fast. Confirm before Phase 10.
5. **Gmail/Outlook credentials.** No Google Cloud project, OAuth client or
   Workspace policy answer is available yet. Phase 7 will ship mocked provider
   adapters and a manual test script; real OAuth needs the Owner to create the
   client and provide `RIB_GOOGLE_OAUTH_CLIENT_ID`/`_SECRET` in `.env`.

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
