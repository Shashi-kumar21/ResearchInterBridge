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

## 11. Superseded by the 2026-10-31 spec rewrite (do not re-litigate)

The Owner rewrote `task.md` into a hosted-product spec (v2, 15 sections, phases
0–13). Two §7 conflict resolutions from this audit are retired:

| Old resolution | Now |
| --- | --- |
| §7.1 Python 3.10 vs 3.11 | still **3.11 minimum**, but the waiver is now explicit in `task.md` §2 (ADR-009): Python 3.10 reached end-of-life 2026-10-01 |
| §7.4 frontend deferred to Phase 10 | **retired** by ADR-010: Next.js + TypeScript starts with Phase 2 and grows with the backend |

The phase numbering in §8 above is the old 11-phase plan. `task.md` §9 (13
phases) is authoritative; the mapping is: old 3 → new 4, old 4 → new 5, old 5 →
new 6, old 6 → new 8, old 7 → new 9, old 8 → new 10, old 9 → new 11, old 10 →
new 12, old 11 → new 13. New phases 3 (documents/CV facts) and 7 (CV
optimization) have no predecessor.

## 12. Phase 2 delivery log (slices merged into `dev`)

| Slice | Scope | Status |
| --- | --- | --- |
| 2a | Student profile model, validation, per-user storage, `user-create` | merged (PR #5) |
| 2b | Document upload/validation/storage, PDF/DOCX/TXT parsing, candidate facts | merged (PR #7) |
| 2c-1 | Fact confirmation, conflict flags, corrections, apply-to-profile | merged (PR #8) |
| 2c-2 | Accounts, scrypt password hashing, lockout, versioned consent, sessions, export/delete | merged (PR #9) |
| 2d-1 | Next.js + TypeScript shell: landing page, honest capability model, frontend CI job | merged (PR #10, ADR-010) |
| 2d-2 | FastAPI auth/consent/account endpoints, cookie sessions, CORS + origin guard, API tests | merged (PR #11, ADR-012) |
| 2d-3a | API hardening: per-address rate limits, bounded scrypt, purge ordering, scheme-aware origin guard | merged (PR #12) |
| 2d-3b | Register, sign-in and consent screens wired to the JSON API | merged (PR #13, ADR-013) |
| 2d-3c | Hydration gate on the account forms, `form-action 'none'`, SSR regression tests | merged (PR #14) |
| 2d-4a | Content API: profile, document upload/parse/delete, fact confirm/apply | merged (PR #15, ADR-014) |
| 2d-4b | Onboarding/profile, document upload/list and fact-review screens | merged (PR #16, ADR-015) |
| 4a | Shared research cache, program validation/provenance, offline `discover` | merged (PR #17, ADR-016) |
| 4b-1 | Cache-only browse reads + research API (programs, institutions, departments) | merged (PR #18, ADR-016) |
| 4b-2 | Research browse screen: institution → department → program, filters, stale labels | merged (PR #19, ADR-016) |
| 4b-3a | Polite live-source HTTP boundary + explicit JSON-LD page parser | this PR (ADR-017) |

Phase 2c-2 notes for reviewers:

* Consents are versioned (`consent.POLICY_VERSION`), so changing the policy text
  re-asks instead of silently reusing an old acceptance.
* `document_processing` consent is enforced inside `documents.save_document`, not
  only in the CLI, so no caller can bypass it (§3.1).
* Only a SHA-256 of each session token is stored; the raw token is returned once
  and written to `data/session` with mode `0600` where the OS supports it.
* Failed logins are indistinguishable for unknown email and wrong password, and
  audit rows never contain the address, the password or the token.
* Still open: Google OAuth client and Workspace policy for phase 9 (ADR-006),
  and the polite-pool contact email for OpenAlex/Crossref (phase 4).

## 13. Phase 2d-1 notes (frontend shell)

* `web/` is a Next.js 16 + React 19 + TypeScript 5.9 app with no UI framework:
  plain CSS tokens and server components only. No client components, no
  hand-written browser JS, no external fonts, images or analytics, so the build
  works offline. Next still ships its framework runtime (~171 KB gzipped here),
  which the README states rather than claiming zero JavaScript.
* Product copy that could over-claim is typed data in `web/lib/roadmap.ts`.
  `web/lib/roadmap.test.ts` fails if email sending becomes `available`, if an
  `available` capability claims the `web` surface (canary until a real web
  feature lands), or if any entry loses its detail.
* Security headers (`nosniff`, `X-Frame-Options: DENY`, referrer policy,
  permissions policy, and the nonce-free CSP subset: `base-uri`, `object-src`,
  `frame-ancestors`, `form-action`) are set in `next.config.ts`.
  `react/no-danger` is an ESLint error for when scraped professor/paper text
  arrives; `script-src` waits for a per-request nonce with the dynamic pages.
* Status and surface copy is data (`roadmap.ts`) with a pure `surfaceLabel()`,
  so a card cannot say "Planned" and "Available on" at the same time. The tests
  also cover the positive case of the web-capability canary.
* CI gets an additive `frontend` job (lint → typecheck → test → build); the four
  required check names are unchanged, per ADR-010.
* Deferred deliberately: no registration/login form until the HTTP API exists, so
  nothing on the site can fail silently.
* QA's licence note is closed by ADR-011: the repository is MIT licensed, so the
  "free and open source" copy on the page is now true.

## 14. Phase 2d-2 notes (JSON API)

* `api/` is a thin layer: routes call `auth`, `consent` and `health`; no rule is
  re-implemented. `create_app(settings)` is the only entry point, so tests and
  `serve` build the same app from the same settings.
* Sessions use an `HttpOnly`, `SameSite=Lax`, host-only cookie. Only the
  SHA-256 of the token is stored. `Secure` follows `RIB_COOKIE_SECURE` because
  local HTTP cannot set it.
* Two independent cross-site defences: exact-origin CORS (empty by default) and
  an origin guard that rejects unsafe methods carrying an unexpected `Origin`
  before authentication runs. A blocked request cannot lock an account.
* Error bodies are `{"error": {"code", "message"}}`; `AuthError`/`ConsentError`
  gained a stable `code`, and the route module owns the code → status table.
  Validation errors keep the field name and reason but never echo the value, so
  a rejected password is not reflected.
* Unauthenticated probe `/api/health` reports only `ok`, both schema versions
  and the check names: no filesystem path, no exception text. Operators get the
  full report from the CLI `health` command.
* `GET /api/auth/export` and `DELETE /api/auth/account` expose the existing
  export/delete behaviours over HTTP. Deletion re-checks the password, removes
  profile rows, facts, consents, documents (files included) and every session.
* Known gap, recorded in ADR-012: no per-IP throttling on `register`/`login`
  yet; the per-account lockout is the only credential-stuffing bound. Addressed
  in §15 (Phase 2d-3a).
* Frontend screens are deliberately not in this slice (ADR-010 "backend-only
  slice" note). The landing page copy was corrected in the same PR because it
  promised "CLI commands for export/delete with the HTTP API"; the endpoints now
  exist, so the sentence names the API and the planned account screens.

## 15. Phase 2d-3a notes (API hardening)

Addresses the review of PR #11. No new endpoint and no new setting; the slice
makes the existing `register`/`login` safe to expose.

* Migration 5 adds `rate_limit_counters(bucket, subject, window_start,
  expires_at, count)`. `ratelimit.py` implements a fixed window with
  `INSERT … ON CONFLICT DO UPDATE` plus a read-back inside one
  `BEGIN IMMEDIATE` transaction, so concurrent requests cannot read the same
  count and a restart does not reset a budget. Refused requests keep counting.
* `register` allows 30/hour and `login` 120/15 min per transport peer address.
  The check runs before `auth.register`/`auth.authenticate`, therefore before any
  scrypt call. The subject is a SHA-256 of the address and the key is the ASGI
  peer (`request.client`); with uvicorn `--proxy-headers` it comes from
  `X-Forwarded-For`, so `--forwarded-allow-ips` must name only the proxy.
* Errors gained headers: `RateLimitError` → `429 rate_limited` with
  `Retry-After`, `account_locked` → `429` with `Retry-After`, and a failed file
  purge → `409 deletion_incomplete`. `error_response` accepts headers, and the
  generic HTTP-error handler forwards Starlette's own headers.
* scrypt shares a per-process `BoundedSemaphore(4)`; the bound is on hashing work, so a
  login flood queues threads instead of multiplying ~16 MiB allocations.
* `documents.purge_user_documents` removes files **before** rows and raises
  `DocumentPurgeError` naming ids when a file is locked, so account deletion
  aborts with the account intact and is retryable. The failure is audited as
  `document.purge_failed` without paths.
* `same_origin` compares scheme as well as host:port, the guard is parametrized
  over `POST /consents`, `POST /logout` and `DELETE /account`, and
  `RIB_CORS_ORIGINS` entries are lowercased so a browser origin matches the
  configured one regardless of how the operator typed it.
* `register`/`login` responses are `Cache-Control: no-store`. The login docstring
  now states the residual `account_locked` oracle instead of claiming the
  endpoint cannot be used for enumeration at all.
* Tests: window expiry, per-client budgets, stored digest, sweep behaviour,
  short-window sweep not deleting a live long-window counter, route-level 429 +
  `Retry-After`, limiter-before-credentials, no-store, host-only cookie,
  scheme-aware origin guard, purge failure and retry, and the scrypt bound.

## 16. Phase 2d-3b notes (account screens)

* Three client components (`components/register-form.tsx`, `login-form.tsx`,
  `consent-manager.tsx`) are the only browser-side code. Every other page stays a
  server component; the shared header is one too.
* `web/lib/api.ts` is the single client. It sends `credentials: "include"`,
  converts the error envelope into `ApiError` with the stable code and
  `Retry-After` seconds, and never reads or stores a session token.
* The registration form fetches `GET /api/consents/catalogue`; the exact server
  texts are rendered, and submit stays disabled until they load and the two
  required kinds are ticked. No consent text is duplicated in the site.
* `/login` maps unknown email and wrong password to one sentence; `429` quotes
  the wait. `/consent` reads the ledger, grants a missing kind, and signs out
  through `POST /api/auth/logout`; signed out it links to the forms.
* Backend change in the same slice: `Access-Control-Expose-Headers:
  Retry-After`, otherwise a browser hides the header from the sign-in form. Tested
  in `tests/test_api.py`.
* Roadmap copy corrected as QA requested in PR #11: the account card no longer
  says "everything you uploaded" (export returns account data, not file
  contents), the account surface is `api` (new label "the HTTP API"), and the
  website card is now `available` on `web` because the screens exist. The web
  canary test was flipped deliberately and still proves the predicate is not dead.
* Frontend tests: 61 total. New: API client (URL, JSON body, credentials, error
  envelope, `Retry-After`, 204, non-JSON, network failure), error copy, roadmap
  honesty, jsdom/Testing Library tests for all three forms (including a grant
  whose follow-up refresh fails and a 401 while granting), server-rendered HTML
  proving the submit buttons cannot fire natively before hydration, and
  `lib/security-headers.test.ts` pinning `form-action 'none'`.
* Dev-only dependencies: `@testing-library/react`, `user-event`, `jest-dom`,
  `jsdom`; justified in ADR-013 because form behaviour cannot be seen by pytest.
  `engines` now names the real floor (`^22.13.0 || ^24.0.0 || >=26.0.0`), which
  is the intersection of vitest's and jsdom's ranges; `.nvmrc`/CI use Node 24.

## 17. Phase 2d-4a notes (content API)

* `PUT /api/profile` is a full replacement. The body cannot set `user_id`,
  `consent_given_at` or the timestamps: unknown fields are rejected with `422`,
  and the consent timestamp is read from the current-policy ledger row. This is
  the invariant behind "only facts the student confirmed may be used": the API
  never records a consent the student did not give.
* `GET /api/documents` and friends are owner-scoped in the service. A foreign id
  answers `404 document_missing`, not `403`, so the API does not confirm that a
  document exists for someone else.
* Uploads stream through a 1 MiB-chunk reader that stops at
  `documents.MAX_DOCUMENT_BYTES`, so an oversized file is refused with `413`
  before this process holds the whole body. `python-multipart>=0.0.30` was added
  for Starlette's `UploadFile`; the floor avoids the quadratic urlencoded parser
  in `GHSA-5rvq-cxj2-64vf`, which Starlette runs before route authentication.
  The service still performs every extension, content, size, consent and dedupe
  check. Identical bytes return the existing row. Starlette spools the parsed
  body before the endpoint, so the cap bounds memory, not pre-auth disk use;
  that limit is documented rather than hidden (loopback deployment today).
* Deleting a document unlinks the file before deleting the row. A locked file
  answers `409 deletion_incomplete` and keeps the document, so the student can
  retry; the earlier order could orphan a file that account deletion never sees.
* Parsing is idempotent and leaves every candidate `pending`. `PATCH
  /api/facts/{id}` confirms (with an optional correction) or rejects one
  candidate. Confirming a candidate makes it the field's only confirmed truth:
  competing pending and previously confirmed rows are rejected, and confirming
  an already-confirmed row with a correction applies the new value. Re-parsing
  skips a field already confirmed from that document, so a correction is not
  resurrected as a new pending candidate.
* `POST /api/facts/apply` copies confirmed values into blank profile fields only,
  unless `overwrite` is explicit, and refuses an unknown field name with `400`
  instead of answering `200` with nothing applied.
* Stable codes: `profile_missing`/`fact_missing`/`document_missing` (404),
  `invalid_profile`/`invalid_document` (400), `document_too_large` (413),
  `unsupported_document` (415), `document_unreadable` (422), plus the existing
  `consent_required` (403) and `deletion_incomplete` (409).
* CORS now allows `PUT` and `PATCH`; the origin guard needed no change because it
  is method-based. All content responses are `Cache-Control: no-store`.
* Tests (48 in `tests/test_api_content.py`): every route without a session, a
  second student on the same database, `PUT` replacement semantics, ledger-sourced
  consent, invalid values, unknown body fields, consent-gated upload, dedupe,
  unsupported/empty/oversized uploads, blank-PDF parse failure, owner-scoped
  read/parse/delete, file removal, a locked file and retry, confirm/reject/
  correct/apply, repeated correction, a previously rejected candidate becoming
  the single truth, overwrite semantics, unknown apply fields, foreign facts,
  `no-store`, CORS preflight for `PUT`/`PATCH`, and cross-origin writes blocked
  before authentication.
* Deferred to 2d-4b: the screens that call these routes. The frontend client and
  its tests are unchanged in this slice.

## 18. Phase 2d-4b notes (content screens)

* Three pages and four components complete the browser side of the content API:
  `/onboarding` (`profile-form.tsx`), `/documents` (`document-manager.tsx`) and
  `/facts` (`fact-review.tsx`), plus shared signed-out/retry/loading panels in
  `account-panels.tsx`. Only these six session-dependent screens are client
  components; the pages, header and landing page stay server-rendered.
* `web/lib/api.ts` now supports `PUT`, `PATCH` and `DELETE`, and sends a
  `FormData` upload without a `Content-Type` header so the browser can add the
  multipart boundary. It gained typed profile, document and fact responses and
  keeps the stable server error code on `ApiError`.
* `GET /api/profile` answering `404 profile_missing` is rendered as a new blank
  form, not an error. Saving always sends a full replacement (`PUT`), including
  `null` for cleared optional values; the server still fills `user_id`,
  `consent_given_at` and the timestamps. The completeness percentage and missing
  labels come from the server response.
* The document screen uploads, lists, parses and deletes. A failed parse is
  persisted by the server, so the list is refreshed after the error and the
  document keeps the reason. Delete asks for confirmation and a `409
  deletion_incomplete` (locked file) keeps the row and explains that the file is
  in use. Deleting also removes the document's non-confirmed facts in the same
  transaction (confirmed facts are profile data and stay), so a pending candidate
  cannot be confirmed through a stale id after its file is gone. Identical
  uploads still dedupe server-side; the notice uses the returned row's status, so
  a re-uploaded file that is already parsed is not described as new.
* The fact screen shows every pending candidate, flags a field whose documents
  disagree, and sends confirm/reject as a `PATCH`. After one decision it reloads
  the whole list because confirming makes that candidate the only truth for the
  field and rejects competing rows in the same transaction. A confirmed fact has
  no unconfirm control because the server forbids rejecting it, but it can be
  corrected in place: "Correct" re-confirms the row with a new value. A field
  whose source document was deleted says so, and corrections are trimmed before
  sending. The fact API sends `source_name` (the document's display name, `null`
  once deleted) so the screen never shows an internal `document:3` id.
* `POST /api/facts/apply` is called with `{fields: null, overwrite}`: blank
  profile fields are filled by default, and the overwrite checkbox is the only
  way to replace a value the student typed. A missing profile keeps the API's
  `profile_missing` message so the page can say to save it first.
* Error copy for the new codes lives in `web/lib/errors.ts`:
  `profile_missing`, `invalid_profile`, `document_too_large`,
  `unsupported_document`, `document_unreadable`, `document_missing`,
  `fact_missing` and `deletion_incomplete`. The `document_too_large` sentence
  names no size, because the limit lives in `documents.MAX_DOCUMENT_BYTES` and a
  hardcoded number would drift. A `401` on load or during an action returns to
  the signed-out panel shared by all three screens.
* Repeated rows carry the same visible control names ("Confirm", "Reject",
  "Delete", "Extract facts"), which is ambiguous for a screen reader and for
  voice control. Each row's controls therefore have an explicit accessible name
  that includes the value or file name (`Confirm ada@example.edu`,
  `Delete ada-cv.pdf`, `Correction for ada@example.edu (optional)`), while the
  visible label stays short. The tests query those names, so a duplicate would
  fail them.
* Product copy was corrected in the same slice: the profile, documents and facts
  cards now say they run on the website, the website card no longer says content
  screens are "still to come", and the landing note names the new capabilities.
  `roadmap.test.ts` asserts the new surfaces and the absence of that phrase.
* Frontend tests: 109 total (up from 61). New: API client `PUT`/`PATCH`/`DELETE`,
  multipart upload without a JSON content type, profile/client error codes;
  content error copy; byte/date formatting; Testing Library tests for the profile
  (signed out, new profile, full replacement, stored profile, server validation,
  retry), documents (signed out, empty, upload, deduped re-upload, parse, failed
  parse, delete, cancelled delete, locked-file failure, `401` during an action),
  and facts (signed out, empty, conflict and correction, source labels, reject,
  correct a confirmed fact, apply with overwrite, non-applicable gating, missing
  profile, retry, `401` during an action).
* The Python half of the same fix is covered by a cascade service test (pending and
  rejected rows removed, confirmed row kept, audit count recorded) and an API test
  (`GET /api/facts` loses the candidates, a stale `PATCH` answers `404
  fact_missing`, `source_name` is `null` after deletion).
* Checks run in this slice: `npm run lint`, `npm run typecheck`, `npm test`
  (109 passed) and `npm run build` (all nine routes prerender); `ruff check`,
  `ruff format --check`, `pytest`.

## 19. Phase 4a notes (shared research cache)

* Migration 6 adds `institutions`, `departments`, `programs`, `program_sources`,
  `research_cache_entries` and `research_query_index` (`task.md` §7, §13.1). No
  table carries a `user_id`: public research data is shared by design (ADR-007).
* `research_cache.py` owns the canonical key, the §13.3 TTL table, content hash
  and provenance. `discovery.py` owns query normalisation/fingerprinting,
  adapter records, validation, storage and the refresh decision.
* A fresh query performs zero adapter calls, including a repeated query whose
  previous answer was empty (cached for one day). A stale query refreshes once;
  a failed refresh keeps the previous rows, marks the entries stale with
  `last_error`, marks their derived institution/department entries failed too,
  and writes `research.refresh_failed` with the error class only. The returned
  rows are reloaded after the failure so every per-program `stale` flag matches
  the result. A failed source is not retried for an hour.
* `DiscoveredProgram` treats absent values as unknown (`None`, empty tuple,
  `unverified`). Validation rejects bad URLs, impossible date ranges, unknown
  statuses and out-of-range confidence; it never fills a missing field. A JSON
  `null` list is treated as no items and duplicate source URLs are collapsed, so
  an adapter payload cannot break the `program_sources` uniqueness constraint.
  Stored programs therefore distinguish "not stated" (NULL) from a stated
  negative. Degree level is stored so the discovery filter can match it offline;
  an unstated level is NULL and never matches a stated filter.
* Program TTLs: 14 days outside an application window, 3 days inside it, 1 day
  within a week of the closing date (or after it), so a closed status cannot be
  served from an old entry for long.
* Institution and department cache entries are derived from the program page in
  this slice; the browse hierarchy (§13.2) will give them their own pages. An
  adapter's raw payload is referenced by path only and must stay outside Git.
* `discover --offline` browses the cache without an adapter and applies its
  filters to the local rows (indexed or not), so a filter works even when that
  exact query never reached a source and an indexed answer cannot override the
  stored fields. No live adapter is configured yet, so an online query with an
  empty cache exits 1 with an actionable message instead of pretending to
  search. The polite-pool contact email is needed for the next slice (4b), not
  this one.
* Migration 6 gained `programs.degree_level` while the branch was under review,
  before merge. A database created from an earlier commit of this branch is at
  version 6 without that column; recreate it. No released schema depends on it.
* A program the source stops listing stays in the cache, marked stale after its
  TTL. 4a does not delete it: a partial refresh must not remove verified data.
  The browse UI treats cache rows as orientation, never as verified truth.
* Tests: 47 new (`tests/test_research_cache.py`, `tests/test_discovery.py`)
  covering key/TTL boundaries, validation (including duplicate sources, null
  lists and null sources), provenance, zero-call cache hits, refresh-in-place,
  stale fallback after a failure (mixed TTLs), retry backoff, empty-result
  caching, offline browsing and filtering (indexed and unindexed), batched reads
  under a low SQLite parameter limit in both modules, degree-level identity,
  cross-connection reuse and the CLI paths. Full suite: 506 passed, 2 skipped.

## 20. Phase 4b-1 notes (cache-only browse reads and research API)

* No migration: 4b-1 reads the tables migration 6 already created. A browse
  request never accepts an adapter, so it cannot call a source (§13.2.2/5).
* `browse_programs`, `browse_institutions` and `browse_departments` live in
  `discovery.py` next to the search path. The offline search and the browse
  filters share `_program_matches`, so country/degree equality, department/area
  substring matching and "NULL never matches" cannot drift between CLI and API.
* Pagination is explicit (`limit` 1–100, default 25, `offset` ≥ 0) and each
  page reports the total the filters matched. Sorting is by name, then id, so a
  page is stable across calls.
* Every program already carried `last_checked_at`/`stale`. Institutions and
  departments now derive the same pair from their cache entries; no entry means
  stale, never "fresh because unknown". A failed refresh marks the derived
  institution/department entry too (4a behaviour).
* Degree levels are canonicalised (`MSc`, `Master of Science` → `master`) in
  validation and in the filter, so identity, the stored value and the filter
  agree. An unrecognised label keeps its own text instead of being forced into
  an "other" bucket.
* API additions: four authenticated, `Cache-Control: no-store` routes under
  `/api/research`. `DiscoveryError` maps to 400, except `program_missing` and
  `institution_missing` (404) and `discovery_unavailable` (503). The browse
  routes do not raise the last one, but the mapping exists so a later search
  route cannot turn a missing adapter into a 500.
* Tests: 30 new (`tests/test_browse.py`, `tests/test_api_research.py`) covering
  paging and totals, every filter and their AND semantics, unknown-value
  exclusion, page-bound validation, program/institution 404s, institution
  counts, department scoping, empty pages, staleness at all three levels,
  adapter-free reading, canonical degree values, session requirement, query
  params and the error envelope. Full suite: 536 passed, 2 skipped.
* Blocked for 4b-3: the concrete live adapter still needs the Owner's
  polite-pool contact email and provider choice. 4b-2 (web screens) can proceed
  against these read-only endpoints.

## 21. Phase 4b-2 notes (research browse screen)

* New `/research` route and `ResearchBrowser` client component. It reads only
  `/api/research/*`: there is no adapter, search button or source call in the
  browser bundle, so a source cannot be contacted from the screen (§13.2.2/5).
* The screen drills institution → department → program. Country filters the
  institution list; country, degree and area filter programs; a department
  selection scopes programs by `department_id` and a breadcrumb returns to the
  whole institution or all institutions. Each level pages with `limit`/`offset`
  and shows the API's `total`.
* Every row keeps `last_checked_at`, a `Cached`/`Stale` badge and the official
  link; a stale program detail states it is orientation only, not verified for
  outreach or a deadline decision. An unstated field renders as "Not stated"
  rather than being filled in, and an empty cache has explicit copy instead of a
  fabricated result.
* Opening a program's details re-reads `GET /api/research/programs/{id}` so the
  panel shows the latest cached copy; the list row is never treated as complete.
* The signed-out, failed-load and empty states reuse the shared account panels
  and the existing `describeApiError` copy. The failed state offers a retry.
* `web/lib/roadmap.ts` now marks research discovery `in-progress` on the web
  surface (cache browsing works; the live adapter and faculty records do not),
  and the landing-page summary counts follow.
* Tests: 10 new frontend tests (`components/research-browser.test.tsx` and the
  discovery status assertion in `lib/roadmap.test.ts`) covering the signed-out,
  list, drill-down, department, filter/reset, detail fetch, empty, paging and
  failure states. Frontend suite: 119 passed. Backend suite unchanged: 536
  passed, 2 skipped.
* Blocked for 4b-3: the concrete live adapter still needs the Owner's
  polite-pool contact email and provider choice.

## 22. Phase 4b-3a notes (polite live-source boundary)

* New `http_source.py`: one GET per URL, with a contact-bearing
  `User-Agent` and `From` header, `RIB_REQUEST_TIMEOUT_SECONDS`, a 2 MiB body
  cap, HTTP(S)-only URLs and no credential-bearing URLs. A non-2xx response, an
  oversized body, a cross-scheme redirect or a transport failure raises
  `SourceFetchError` naming the host and status; the response body is never in
  the message.
* New `program_pages.py`: reads only schema.org JSON-LD
  `EducationalOccupationalProgram`/`JobPosting` blocks. Explicit fields are
  mapped; an unstated deadline, stipend, method or document list stays unknown,
  status starts `unverified` and confidence is unscored. ISO dates only; prose
  dates are ignored. `Course` is excluded. A malformed block is skipped without
  hiding a valid block next to it; pages are read sequentially so a failure is
  not hidden by a partial result.
* New setting `RIB_RESEARCH_CONTACT_EMAIL` (blank by default). Constructing the
  HTTP client without a valid single address is a `ConfigError` before any
  request. `.env.example` documents it and keeps it blank, so a fresh checkout
  cannot contact a source by accident.
* No CLI or API wiring and no live request in this slice. The adapter that
  connects parsed pages to `discovery.search_programs` still needs a source
  choice; OpenAlex is a Phase 5 scholarly source, not an internship catalog.
* Tests: 32 new (`tests/test_http_source.py`, `tests/test_program_pages.py` and
  the contact-email config cases) covering contact/UA headers, unsafe URLs,
  status and size failures, charset decoding, JSON-LD/`@graph` mapping, unknown
  fields, malformed blocks, URL fallback, invalid dates, the item cap, sequential
  loads and config validation. All HTTP is injected, so the suite opens no
  socket. Full suite: 568 passed, 2 skipped.
