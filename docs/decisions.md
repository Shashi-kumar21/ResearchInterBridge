# Architecture decisions

Short, dated decisions that unblock agents and are cheap to revisit. Any change
here needs the Owner's agreement; the reasoning is recorded so the next agent
does not re-litigate it.

## ADR-001 — Python floor is 3.11 (supersedes `task.md` §0)

**Status:** accepted 2026-10-30 · **Decided by:** Owner (delegated to Dev)

`task.md` §0 says "Python compatibility: Python 3.10.x", while `pyproject.toml`
(`requires-python = ">=3.11"`, Ruff `target-version = "py311"`), `AGENTS.md` §5
("Python >= 3.11") and CI (3.12) all say 3.11+.

**Decision:** the floor is **3.11**; `task.md` §0 is superseded. Python 3.10
reached end-of-life on **2026-10-01** (no further security fixes), and this
product stores OAuth tokens and student documents, so an EOL interpreter is not
acceptable.

**Consequences:** `datetime.UTC` is used instead of the `timezone.utc` workaround;
CI also runs the suite on 3.11 (`test-python311`, additive check — the four
required check names are untouched); local commands use `py -3.11` because the
machine's default `python` is 3.10.11.

## ADR-002 — Backend-first until Phase 10

**Status:** accepted 2026-10-30 · **Superseded by ADR-010** (2026-10-31)

No frontend framework is added before the dashboard phase. Phases 2–9 ship behind
CLI/tests so CI stays fast and fully offline. `task.md` §3 offers Next.js only if
no UI exists; adding it earlier would mean maintaining an unverified UI on top of
services that do not exist yet.

## ADR-003 — `task.md` is committed to the repository

**Status:** accepted 2026-10-30

The specification is the source of truth and the QA worktree cannot see an
untracked file. It is committed verbatim; the Owner remains its author.

## ADR-004 — Credential redaction is not configurable

**Status:** accepted 2026-10-30

There is no `RIB_REDACT_LOGS` switch. Redaction runs in both a log filter and a
formatter, so any handler added later inherits it. A debugging escape hatch would
be the fastest route to a leaked OAuth token in a log file.

## ADR-005 — Health probes are read-only

**Status:** accepted 2026-10-30

`health` opens the database with `mode=ro` and never creates tables, so it is
safe as a monitoring/readiness probe and cannot mask a broken migration by
silently creating bookkeeping tables.

## ADR-006 — OAuth credentials are blocked on the Owner

**Status:** open

A Google Cloud project, an OAuth client and the institutional Workspace policy
answer do not exist yet. Phase 9 will therefore be built and tested against
mocked provider adapters first; live connect/disconnect needs
`RIB_GOOGLE_OAUTH_CLIENT_ID` and `RIB_GOOGLE_OAUTH_CLIENT_SECRET` in `.env`.
No credential will ever be committed, logged or shared through the bridge.

## ADR-007 — Shared cache covers public research data only

**Status:** accepted 2026-10-30 · **Spec:** `task.md` §13

Programs, faculty, publications and source URLs are cached once and reused for
every student, so the same "university → department → faculty" lookup is not
re-run per user. Profiles, documents, confirmed facts, drafts, approvals,
outreach records and OAuth credentials stay per-user and are never reused or
exposed across students. Cross-user reuse of a *result set* requires a bucket of
at least three profiles and is written to `audit_events` without personal
content.

**Consequences:** cache keys are normalised (university, department, query
fingerprint) and every cached row carries `source_url`, `fetched_at`,
`verified_at` and a TTL from the `task.md` §13.3 table. A stale entry is shown as
stale, never as verified. A repeat query must perform zero outbound calls.

## ADR-008 — Deliverability is best-effort, never promised

**Status:** accepted 2026-10-30 · **Spec:** `task.md` §14

Mail is sent through the student's own Gmail account, so SPF/DKIM/DMARC and
inbox placement are outside the product's control. The product must never claim
an email will avoid spam. It enforces what it does control: deterministic content
lint, unique per-recipient personalization, per-account daily ceiling, spacing
with jitter, warm-up ramp, suppression list and honest status reporting.

## ADR-009 — Python 3.11 minimum confirmed; 3.10 compatibility waived

**Status:** accepted 2026-10-31 · **Decided by:** Owner · **Spec:** `task.md` §2

The Owner re-added "Python 3.10.x compatibility must be preserved" in the
rewritten spec. Review of the release calendar (python.org/devguide, checked
2026-10-31) shows 3.10 end-of-life on **2026-10-01** (3.11 security support runs
to 2027-10, 3.12 to 2028-10), and every planned dependency (PyMuPDF,
python-docx, FastAPI, psycopg, Celery) supports 3.11+.

**Decision:** the floor stays **3.11**; the 3.10 requirement is explicitly
waived rather than silently ignored. Owner's instruction: use 3.10 only if it
were mandatory — it is not.

**Consequences:** no 3.10 shims; CI keeps 3.11 (`test-python311`) and 3.12; a
later jump to 3.13/3.14 is allowed after a dependency and CI-matrix review.

## ADR-010 — Next.js frontend starts with Phase 2, not Phase 12

**Status:** accepted 2026-10-31 · **Decided by:** Owner · **Spec:** `task.md` §5, §9

`task.md` now requires a public hosted website with Next.js + TypeScript, built
incrementally alongside the backend APIs. ADR-002 (backend-first until the
dashboard phase) is therefore retired.

**Consequences:** phases 2+ ship a screen (or an explicit "backend-only slice"
note in the PR) instead of deferring all UI; frontend lint/typecheck/build joins
CI as *additive* jobs so the four required check names stay stable.

## ADR-011 — MIT license

**Status:** accepted 2026-11-02 · **Decided by:** Owner · **Spec:** `task.md` §1

The repository claims to be free and open source but carried no license, which
means the default "all rights reserved" applied: public visibility is not an open
source grant. The Owner chose **MIT**.

**Why MIT:** the product must stay easy to self-host by students and small labs,
and MIT adds no attribution burden beyond keeping the notice. A copyleft license
(AGPL) was considered because the product is also hosted, but it would raise the
bar for the self-hosting mode the spec requires.

**Consequences:** `LICENSE` is committed and `README.md` links to it. Third-party
dependencies keep their own licenses; a dependency whose license is incompatible
with MIT must be rejected in review. Changing the license later is an Owner
decision and needs an ADR.

## ADR-012 — FastAPI for the JSON API

**Status:** accepted 2026-11-02 · **Decided by:** Dev (within ADR-010) · **Spec:** `task.md` §5, §9

The web frontend needs an HTTP surface, but every rule (password policy, consent
gate, session lifetime, lockout, deletion) already lives in the service modules.
The API must not grow a second copy of them.

**Decision:** FastAPI, mounted with a factory (`create_app(settings)`), with
Pydantic v2 request models and ``httpx``-based tests. Starlette supplies the
ASGI runtime; uvicorn serves it.

**Why FastAPI:** it is the smallest amount of code that gives request validation,
response models (so a password hash cannot accidentally be returned in a typed
route), an OpenAPI schema for the frontend, and typed dependencies for
"connection per request" and "current session". It is also the stack named in
`task.md` §5, so no second framework decision is deferred.

**Consequences:**

* `fastapi` and `uvicorn` become runtime dependencies; `httpx` becomes a test
  dependency (`pip install -e ".[test]"`). CI installs the extra.
* The API stays a thin layer: routes may only call `auth`, `consent`,
  `profiles`, `documents` and `health`. A rule added to a route instead of a
  service is a review rejection.
* Error codes are part of the contract. `AuthError`/`ConsentError` carry a
  stable `code`, and `api/routes.py` maps each to one HTTP status; an unmapped
  code is a 400 by default rather than a leaked 500.
* The session cookie is `HttpOnly`, `SameSite=Lax` and host-only. `Secure` is
  configuration because local HTTP development cannot set it. Cross-origin
  callers must be listed exactly in `RIB_CORS_ORIGINS`; an unsafe method with an
  unexpected `Origin` is rejected before authentication. The guard compares
  scheme, host and port exactly, so a deployment behind a TLS-terminating proxy
  must forward the original scheme (`--proxy-headers` with a trusted
  `--forwarded-allow-ips` for uvicorn) or browser requests will be refused.
* Unauthenticated `register`/`login` are throttled per transport peer address
  (`ratelimit.py`): 30 registrations/hour, 120 logins/15 minutes, fixed window,
  counters in SQLite. The check runs before scrypt, so a flood costs a row
  update instead of a 16 MiB hash. The subject is a SHA-256 of the address;
  the key is the ASGI peer (`request.client`), so with uvicorn `--proxy-headers`
  it comes from `X-Forwarded-For` and `--forwarded-allow-ips` must name only the
  trusted proxy, never `*`. This is a single-host bound, not a global one: a botnet with many addresses still gets
  one budget each, which is why the per-account lockout and the account-level
  email/consent rules remain. A shared campus NAT also shares one budget, so the
  two constants in `ratelimit.py` are deployment tuning knobs, not sacred
  numbers.
* scrypt calls share a `BoundedSemaphore(4)` **per process**: request threads
  queue instead of each allocating ~16 MiB, so concurrency degrades latency
  rather than memory. `serve` starts one process, so the shipped bound is four;
  a multi-worker deployment would allow four hashes per worker.
  FastAPI runs these synchronous service calls in its worker thread pool, so the
  wait never blocks the event loop.
* Rate-limit knowledge that would otherwise leak: a locked account answers
  `429 account_locked` only after five wrong guesses and only for an address
  that exists. That residual oracle is accepted (the alternative, locking by
  address, punishes shared campus NAT); `429 rate_limited` is uniform and is
  emitted before the account row is read.

## ADR-013 — Browser account screens and the API client

**Status:** accepted 2026-11-04 · **Decided by:** Dev (within ADR-010, ADR-012) · **Spec:** `task.md` §3.1, §5, §9

The API exists (ADR-012) and the site was server-components-only. Students still
had no way to create an account from the hosted site, and a public site that
describes accounts but cannot create one is the over-claim the project keeps
trying to avoid.

**Decision:** ship register, sign-in and consent as the only client components;
keep every other page a server component. `web/lib/api.ts` is the single browser
client, authenticated by the existing HttpOnly cookie with
`credentials: "include"`. The registration form fetches the consent catalogue
from the server instead of holding a copy of the texts. `NEXT_PUBLIC_API_BASE_URL`
selects the API origin at build time (default `http://localhost:8000`).

**Why this shape:**

* Consent is a ledger row against a policy version. A second copy of the text in
  the site could drift, so the page renders what the server will record. If the
  catalogue fails to load, submission stays disabled: nobody can agree to text
  that was not shown.
* The cookie stays the authentication mechanism. No token is stored in
  `localStorage` or exposed to JavaScript, so an XSS on the site cannot read a
  session token; it would still need the browser to make the request.
* Errors are branched on the stable `code` and the HTTP status, never on message
  text. `ApiError` carries `Retry-After` seconds, which the API now exposes via
  CORS (`Access-Control-Expose-Headers: Retry-After`) — without that, a browser
  hides the header and the lockout message could not quote the wait.
* `/consent` reads the server state and signs out through the API. It does offer
  the `gmail_access` consent before OAuth exists because recording consent is
  harmless and the text says the connection arrives later; the scope is not
  requested and no email is sent.

**Consequences:**

* JavaScript is required for the three forms. The submit buttons stay disabled
  until hydration (`useHydrated`, a `useSyncExternalStore` server/client split),
  both forms declare `method="post"`, and `form-action 'none'` in the CSP blocks
  every native submission — so a click before hydration, or with JavaScript
  disabled, cannot put a password in the URL. Nothing posts to a fallback URL.
* The site and API must be same-site for the `SameSite=Lax` cookie in
  development: use `http://localhost:3000` and `http://localhost:8000`, not a mix
  with `127.0.0.1`. Production needs HTTPS and `RIB_COOKIE_SECURE=true`.
* The site's origin must be listed exactly in `RIB_CORS_ORIGINS`, and the
  browser must be able to reach the API URL directly. A same-origin reverse proxy
  (`/api` → backend) remains the simpler production deployment and needs no CORS
  at all.
* `NEXT_PUBLIC_*` is inlined at build time, so changing the API origin is a
  rebuild, not a runtime setting.
* Test dependencies grow: `@testing-library/react`, `user-event`, `jest-dom` and
  `jsdom` (dev-only). They are justified because the forms now carry behaviour
  (consent gating, error mapping, session handling) that the Python suite cannot
  see, and hand-testing it once is not a regression test.

## ADR-014 — Content API for profile, documents and facts

**Status:** accepted 2026-11-04 · **Decided by:** Dev (within ADR-012) · **Spec:** `task.md` §3.2, §3.3

The JSON API could create an account but had no way to save the profile the
onboarding screen collects, upload a CV, or confirm the facts extracted from it.
The CLI and the website therefore could not reach the same feature set.

**Decision:** add a content surface to the same router, again as a thin layer
over the services:

| Route | Service |
| --- | --- |
| `GET/PUT /api/profile` | `profiles.load_profile`, `profiles.save_profile`, `profiles.completeness` |
| `GET/POST /api/documents`, `GET/DELETE /api/documents/{id}` | `documents.*` |
| `POST /api/documents/{id}/parse` | `parsing.parse_document` |
| `GET /api/facts`, `PATCH /api/facts/{id}`, `POST /api/facts/apply` | `facts.*` |

**Why this shape:**

* `PUT /api/profile` is a full replacement: the body is the profile the student
  sees, omitted optional fields are cleared. `user_id`, `consent_given_at` and
  the timestamps are rejected as unknown fields and filled server-side; the
  consent timestamp comes from the consent ledger, so a payload cannot claim a
  consent that was never recorded.
* Uploads are `multipart/form-data`, which requires `python-multipart` at
  `>=0.0.30`: older releases have a quadratic-time urlencoded parser
  (`GHSA-5rvq-cxj2-64vf`), and Starlette runs that parser before route
  authentication. The route drains the upload in 1 MiB chunks and stops at
  `documents.MAX_DOCUMENT_BYTES`, so an oversized file is refused before the
  whole body is held in memory. The bound is memory, not pre-auth disk usage:
  Starlette has already parsed and spooled the complete body before the endpoint
  (and its auth dependency) runs. That is acceptable while the API is bound to
  loopback; a public deployment needs an ASGI-level body limit in front of this
  app, which is recorded rather than pretended.
* Every document read, delete and parse is owner-scoped in the service, so
  another student's id answers `404 document_missing` rather than `403`. That
  avoids confirming that an id exists.
* Deleting a document unlinks the file **before** deleting the row. If the file
  is locked, the row survives and the request answers `409 deletion_incomplete`,
  so a retry is possible; deleting the row first would leave bytes that account
  deletion (which walks rows) could never find again.
* Personal content is `Cache-Control: no-store`, like `/api/auth/me`. A shared
  cache must not keep a CV or a profile.
* Fact confirmation stays a `PATCH` on one candidate with `status` and an
  optional `corrected_value`. Confirming makes that row the only confirmed truth
  for its field, rejecting competing pending **and** previously confirmed rows;
  confirming an already-confirmed row with a correction applies the new value
  instead of silently ignoring it. `POST /api/facts/apply` copies only confirmed
  facts, keeps values the student typed unless `overwrite` is explicit, and
  refuses a field name outside the applicable set instead of ignoring a typo.
  Re-parsing is idempotent: a field already confirmed from that same document is
  skipped, so re-parsing cannot resurrect a value the student corrected.
* Errors carry stable codes mapped next to the routes: `profile_missing` (404),
  `fact_missing` (404), `invalid_profile` (400), `invalid_document` (400),
  `document_missing` (404), `document_too_large` (413),
  `unsupported_document` (415), `document_unreadable` (422) and the existing
  `consent_required` (403) from the document service. Messages name the field,
  extension or limit; they never include document text or a filesystem path.
* CORS now allows `PUT` and `PATCH`, and the origin guard already covers every
  unsafe method because it is method-based, not route-based.

**Consequences:**

* The schema and the service are the only validators; the route does not repeat
  an individual rule. A profile rejected by the domain object is a `400`, not a
  `422`, because the body was well-formed.
* A student who has not saved a profile gets `404 profile_missing`; the
  onboarding screen can tell "not started" from "saved with blanks". Applying
  facts without a profile is a `404` for the same reason.
* The Python set of runtime dependencies grows by one (`python-multipart`).
  It is small, MIT-licensed and only used by the upload endpoint.
