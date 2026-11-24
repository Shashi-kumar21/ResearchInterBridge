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

## ADR-015 — Content screens treat the API as the source of truth

**Status:** accepted 2026-11-04 · **Decided by:** Dev (within ADR-013) · **Spec:** `task.md` §3.2, §3.3

The content API from ADR-014 had no browser surface: a student could register and
review consent, but had to use the CLI for the profile, a document upload and
fact confirmation. That is not the product `task.md` describes.

**Decision:** add three client screens — `/onboarding`, `/documents` and
`/facts` — over the existing routes. They are client components because every
read and write is authenticated server state; the page shell, header and landing
page stay server components. `web/lib/api.ts` gains `PUT`, `PATCH` and multipart
support, and `web/lib/errors.ts` gains the content codes from ADR-014.

**Why this shape:**

* `GET /api/profile` returning `404 profile_missing` means "not started", not
  "broken". The onboarding screen shows a blank form in that case and an error
  panel for every other failure, so the deliberate absence of an invented empty
  row survives all the way to the UI.
* `PUT` is a full replacement, so the form sends every editable field, including
  `null` for a cleared optional value. The server still owns `user_id`,
  `consent_given_at` and the timestamps. The screen never PATCHes a subset and
  hopes the omitted fields survive.
* An upload is a `FormData` body. The shared client deliberately omits
  `Content-Type` for it; setting JSON or even `multipart/form-data` by hand
  breaks the boundary. The list is reloaded from the API, not reconstructed from
  the upload response alone.
* A parse failure is persisted by the server (`status=failed`, `parse_error`), so
  the screen refreshes the list after the error and shows the reason next to the
  document. Hiding a failed row would make a scan look like a lost upload.
* Confirming a fact can reject several competing rows in one transaction. The
  screen therefore refreshes the whole fact list after each decision instead of
  patching one row in local state. A confirmed fact has no "unconfirm" button
  because the server forbids rejecting it, but it has a "Correct" action that
  re-confirms the row with a new value, so a wrong value does not require a
  competing candidate (or the CLI) to fix.
* Applying facts is explicit about `overwrite`. The default fills blank profile
  fields only; a typed value is kept unless the student ticks the overwrite
  checkbox. A missing profile still answers `404 profile_missing`, and the page
  says to save the profile first.
* Every failure branches on `ApiError.code`/`status`: `401` returns to the
  signed-out panel, and content codes get student-facing copy. No profile,
  document or fact response is cached in the browser.

**Consequences:**

* Six client components now exist (register, sign in, consent, profile,
  documents, facts). The `<noscript>` note on each content page says JavaScript
  is required; no content can be read or saved without the API.
* The document bytes are held only long enough to construct the upload request;
  no file or extracted text is written to local storage. The page renders
  server-provided metadata, never a document body.
* The fact screen's "applicable fields" list mirrors `facts.APPLICABLE_FIELDS`
  for its hint. The API remains the authority: a mismatch changes which guidance
  is shown, not what is applied.
* A fact carries the display name of its source document (`source_name`, `null`
  when that document was deleted), so the screen never prints an internal
  `document:3` id. Deleting a document removes its non-confirmed facts in the
  same transaction and keeps confirmed ones, which matches what the delete
  confirmation promises; the audit event records the count.
* The frontend suite grows from 61 to 109 tests. The new coverage is the client
  request shapes (including multipart), content error copy, byte/date formatting,
  profile new/existing/save/retry, document upload/deduped re-upload/parse/delete
  and locked-file failure, conflict display, confirm/reject/correct of a pending
  fact and of a confirmed one, apply with overwrite and its applicable-field
  gating, source labels, and a `401` arriving during an action.

## ADR-016 — Discovery is source-agnostic and cache-first

**Status:** accepted 2026-11-05 · **Decided by:** Dev (within ADR-007, ADR-012) · **Spec:** `task.md` §3.4, §13

Phase 4 starts with no live source configured yet: provider choice and the
polite-pool contact email are still pending, and writing an adapter first would
bake one provider's shape into the schema and invite a network call per query.

**Decision:** build the shared cache and the adapter boundary before any live
source.

* Migration 6 adds `institutions`, `departments`, `programs`, `program_sources`,
  `research_cache_entries` and `research_query_index` (§7, §13.1). None of these
  tables carries a `user_id`: they are public research data (ADR-007).
* `research_cache` owns canonical keys, the §13.3 TTL policy and provenance
  (`source_url`, `fetched_at`, `verified_at`, `content_hash`, adapter, parser
  version, payload pointer, last attempt/error). `discovery` owns query
  normalisation, validation, storage and the refresh decision.
* A `ProgramAdapter` is injected; this slice ships none. `discover --offline`
  browses the cache and applies its filters to the local rows, indexed or not, so
  a filter works even when that exact query never reached a source and the result
  cannot disagree with the stored fields. An online query without an adapter
  fails with an actionable `DiscoveryUnavailableError` instead of pretending to
  search.
* `DiscoveredProgram` defaults to unknown (`None`, empty, `unverified`). The
  validator rejects malformed records (bad URL, impossible date range, unknown
  status, out-of-range confidence) and never fills a gap. A record is stored with
  exactly the values its source stated. A JSON `null` list or source list means
  "no items", and duplicate source URLs are collapsed because the table keeps one
  row per program and URL.
* A program's canonical key includes its degree level: a BSc and an MSc with the
  same name at the same department are two programs, not one colliding row. An
  unstated level is omitted from the key.
* A fresh query performs zero adapter calls. A non-empty result is fresh while
  each program's TTL holds; an empty result is cached for one day so repeating a
  query that found nothing does not call the source either.
* A failed refresh keeps the previous rows, marks their cache entries stale
  (`last_error`) and audits the failure by error class. Derived institution and
  department entries fail with the program page that backs them, and the returned
  rows are reloaded so every `Program.stale` flag agrees with the top-level
  result. Stale rows are still returned, labelled stale, because deleting
  verified data is worse than showing it with an honest timestamp.
* A failed refresh is not retried for one hour (`FAILED_REFRESH_RETRY_SECONDS`).
  Without that window, every repeated query against a broken source would hit
  it again, which §13.3 forbids. The window covers queries that have cached rows;
  a cold query that has never succeeded is not cached as a failure yet. The live
  adapter slice owns that negative marker, because only then can a source fail
  without a stored result.
* The offline department filter also matches the institution name, so
  `--department oxford` finds Oxford programs whose department is named
  differently. Country and degree level stay equality matches.
* The browse reads (`browse_programs`, `browse_institutions`,
  `browse_departments`) are cache-only: they never accept an adapter and never
  call a source. They share `_program_matches` with the offline search path, so a
  filter cannot mean one thing on a screen and another in the CLI. Pagination
  reports the total matched, and every row carries `last_checked_at` and `stale`.
  An institution/department row with no cache entry is stale, never assumed
  fresh; an unknown institution id is a 404 rather than an empty page.
* Institution and department cache entries are derived from the official program
  page for now and refreshed with it. Their own pages are added with the browse
  hierarchy (§13.2).
* 4b-2 renders that hierarchy in the web client: institution → department →
  program, with country/degree/area filters, explicit paging and a stale badge at
  every level. The screen calls only `/api/research/*`, so browsing cannot
  trigger a source fetch; a detail panel re-reads one program instead of trusting
  a list row. An unstated field is rendered as "Not stated", and a stale row
  says it is orientation only, not verified for outreach or a deadline decision.
  The page stays cache-only for the same reason the API is: discovery must work
  when a source is unreachable, and no screen may invent a record.
* A program the source stops listing stays in the cache: it remains browsable
  offline (stale after its TTL) but is never presented as verified. 4a does not
  tombstone or delete it, because a partial or failed refresh must not remove
  verified data. The browse UI must treat these rows as orientation, not as
  current truth; an explicit tombstone can be added with the browse hierarchy.
* Raw payloads are referenced by path only; an adapter that stores one must keep
  it outside Git and under the data directory.

**Consequences:**

* 4b-1 layers the cache-only browse reads and their API on these tables without
  a schema change, and 4b-2 renders them in the browser. 4b-3a adds the polite
  HTTP and explicit-page parsing boundary (ADR-017), and 4b-3b wires it into
  `discover --source-url`; the HTTP API still exposes cache-only reads.
* The cache is already queryable offline, so the browse API and the
  university → department → faculty screens reuse these reads instead of
  duplicating filter rules.
* The one-day empty-result TTL is a product choice made here; it trades a small
  amount of freshness for not hammering a source with queries that rarely
  change.
* The bucket-of-three rule (§13.4) governs personalised result sets in Phase 6.
  Public program/faculty records are shared by design and are not gated by it.

## ADR-017 — Live sources are fetched politely, parsed only from explicit data

**Status:** accepted 2026-11-08 · **Decided by:** Dev (within ADR-016) · **Spec:** `task.md` §3.4, §8, §13.3

The cache and browse screens exist, but a live adapter still has no network
boundary. Two failure modes had to be closed before any provider code: an
anonymous scraper that a university cannot contact or rate-limit, and a parser
that turns prose into a deadline. OpenAlex was the suggested provider, but it
indexes institutions, authors and works, not internships; treating it as a
program source would fabricate records that `task.md` §3.4 does not allow.

**Decision:** ship a provider-agnostic polite HTTP boundary and one explicit page
format, with no live calls until an operator configures a contact address.

* `http_source.PoliteHttpClient` sends a descriptive `User-Agent`
  (`ResearchInterBridge/<version> (+mailto:<contact>)`) and the same address in
  `From`. Constructing it requires `RIB_RESEARCH_CONTACT_EMAIL`; a live fetch
  without a reachable contact is a configuration error before the first request.
* Only `http`/`https`, no embedded credentials, one GET, a timeout
  (`RIB_REQUEST_TIMEOUT_SECONDS`), a 2 MiB body cap and no cross-scheme redirect.
  A non-2xx response or an oversized body raises `SourceFetchError` naming the
  host and status; the body is never included in the message.
* Tests inject a transport, so the suite never opens a socket and CI stays
  offline (§8).
* `program_pages` reads only schema.org JSON-LD blocks typed
  `EducationalOccupationalProgram` or `JobPosting`. A field the page does not
  state stays `None`, status starts `unverified` and confidence is unscored.
  Supported dates must be ISO dates; prose such as "15 January 2027" is ignored.
  `Course` is excluded because it is not evidence of a research placement.
* Configured `ProgramPageSource` records supply the institution (and optional
  country/department). They are operator configuration, never inferred from a
  hostname.
* Pages are read sequentially. A failed page raises instead of returning a
  partial tuple, so the caller can mark the refresh failed rather than present
  half a result as complete.

**Consequences:**

* OpenAlex remains a Phase 5 scholarly source for faculty and publications; it is
  not wired into program discovery. Phase 4 needs official university/department
  pages, which the operator configures.
* `OfficialPageProgramAdapter` implements the existing `ProgramAdapter` protocol,
  and `discover --source-url ...` passes it to `discovery.search_programs`.
  Parsed records are filtered by `discovered_program_matches`, which shares its
  rule with the cache read, so cache and live results cannot drift. Cache writes,
  TTLs and stale marking stay owned by `discovery`.
* The HTTP API still exposes cache-only browse reads. Exposing live search there
  needs its own route and a decision about who may trigger a source fetch.
* The contact address is a deployment value, not a student value. `.env.example`
  leaves it blank, so a fresh checkout cannot contact a source by accident.

## ADR-018 — Faculty identity is the profile URL; contacts and papers need a source

**Status:** accepted 2026-11-24 · **Decided by:** Dev (within ADR-007, ADR-016) · **Spec:** `task.md` §3.5, §13

Phase 5 has to list professors without the two failure modes §3.5 forbids:
merging two people who share a name, and producing an email or paper the source
never stated. Both are easy to introduce by accident: a name-based key merges
people, and a "helpful" adapter rewrites `jane [at] uni.edu` or guesses
`first.last@uni.edu`.

**Decision:** store faculty in the shared cache with identity, contact and paper
rules that are checked in code, before any live source exists.

* A faculty record's canonical key is a hash of its normalised official profile
  URL. The name is display data. A different profile URL is a different person;
  the same URL with a new name is the same person, renamed.
* An email is stored only with `email_source_url`, the page that lists it, and
  only as one plain address. It is `verified` only when **both** hold: the
  adapter declares `contact_authority = "official"` (it reads operator-configured
  institutional pages), and the listing page is on the profile's host (ignoring
  `www.`). Every other case is `unverified`. The host check alone is not enough:
  an ORCID record or a shared host such as `sites.google.com` puts a
  self-reported or another person's address on the same host as the profile (QA
  M1, PR #23). An adapter that declares no authority is `none`; an unknown value
  is a `DiscoveryError` raised before the source is called.
  `contact_sendable` requires a verified address on a fresh (non-stale) record.
  Outreach (Phase 8-10) must use that flag, never the presence of an address.
* A paper is linked to a person only because the source attributed it to that
  profile. Author lists are kept in order as display text and are never used to
  link people. A DOI or a source URL is required; nothing fills a missing year,
  venue or abstract. Without a DOI, the paper's identity is its URL normalised
  like a profile URL (scheme, `www.`, trailing slash and fragment ignored).
* Faculty and publications follow the §13.3 TTLs and the same refresh, backoff
  and failure rules as programs (ADR-016). A failed refresh makes the faculty,
  their papers and the derived institution/department entries stale together.
* `faculty.py` reuses the validation helpers in `discovery.py` (text, URL, page
  bounds, batching), so a program and a faculty record cannot disagree about what
  a valid URL or a page size is.

**Consequences:**

* The host rule is conservative: a profile on `cs.uni.edu` with an address listed
  on `uni.edu/directory` stays `unverified`. Widening it needs a registrable-domain
  rule (a public-suffix list), which would be a new dependency, so it is deferred.
* The same paper first seen without a DOI and later with one becomes two rows.
  Merging them would need a confident match on more than a title, so it is left
  to a later slice rather than guessed.
* A person with two official profile pages (a joint appointment, for example) is
  two rows. That is deliberate: merging needs explicit evidence, not similarity.
* Publication rows are shared. The last successful source to store a paper
  overwrites its title, venue, abstract and author list, so two sources that
  disagree will alternate the row. Per-source publication metadata is deferred
  until a second publication source exists (5c).
* A failed refresh marks a shared paper stale for every faculty member linked to
  it, not only the one whose page failed. That is conservative: a paper is shown
  as verified only while every source that backs it is fresh.
* `browse_faculty` and offline faculty search load every faculty row and filter
  in Python, like the program reads. That is fine at cache sizes today; the
  PostgreSQL phase should push these filters into SQL.

## ADR-019 — Faculty pages are read from explicit schema.org data; ORCID keys papers

**Status:** accepted 2026-11-24 · **Decided by:** Dev (within ADR-017, ADR-018) · **Spec:** `task.md` §3.5, §8

ADR-018 made identity, contacts and paper links safe once a record exists. A
live faculty source still has to produce records without turning prose into an
email or a name search into a paper list. OpenAlex and Crossref index authors
and works, but they do not publish official profile URLs or institutional
addresses, and searching them by name would merge people.

**Decision:**

* Faculty records come from operator-configured official pages, parsed only from
  schema.org JSON-LD `Person` objects, the same explicit-data rule as
  `program_pages` (ADR-017). Nothing is read from HTML prose.
* An email is read only from `email`. A value that is not one plain address is
  left unknown, not repaired. The page that states it is the evidence.
  `OfficialPageFacultyAdapter` declares `contact_authority = "official"`, so
  ADR-018's rule (official authority **and** the profile's host) decides whether
  it is verified. A future ORCID or scholarly-index adapter declares nothing and
  therefore never verifies an address.
* An ORCID iD is stored only when the page states it as an ORCID (an `orcid.org`
  URL or a `propertyID: ORCID` value) and its check digit holds. It is the only
  key a later slice may use to fetch a person's papers from a scholarly index.
* The configured institution, country and department take precedence over page
  values, so the browse hierarchy does not fragment.
* A Person without its own `url` may use the page URL as its identity only when
  that cannot collide: the page describes exactly one person, or exactly one
  `ProfilePage` main entity. Otherwise url-less people are skipped. Without this
  rule every url-less person on a directory page shared one canonical key, so a
  re-ordered directory "renamed" a stored row to someone else and attached that
  person's verified address to it (QA M1, PR #24).

**Consequences:**

* A department that publishes no JSON-LD yields no faculty. That is the honest
  result; supporting another explicit format needs its own parser and review.
* A directory page that lists people without their own profile links yields none
  of them. Their identity is not stated, so it is not invented; the operator can
  configure each person's own page instead.
* A Person's `url` on a host other than the configured page is recorded as a
  `linked_page` source, not `official_page`. Contact verification is unaffected:
  it already requires the profile's host (ADR-018).
* `www.orcid.org` is accepted as an ORCID prefix alongside `orcid.org`.
* Papers for a person without a stated ORCID iD stay empty until a source
  attributes them explicitly. A name-based author search is not a fallback.
* 5c can call OpenAlex `/authors/orcid:<id>` and `/works?filter=author.orcid:<id>`
  through the existing polite client, with no new dependency.

## ADR-020 — Papers come from OpenAlex only by stated ORCID, re-checked per work

**Status:** accepted 2026-11-24 · **Decided by:** Dev (within ADR-017 to ADR-019) · **Spec:** `task.md` §3.5, §8

Faculty pages rarely list papers in structured form, and §3.5 asks for two or
three recent publications with DOI or an official link. OpenAlex covers that and
is free and polite-pool friendly, but an author search by name merges people.

**Decision:**

* OpenAlex is consulted only for a person whose official page stated an ORCID iD
  (ADR-019), using `/works?filter=author.orcid:<id>`. No name, institution or
  free-text search is ever sent.
* Every returned work is re-checked: it is kept only if one of its authorships
  carries that exact ORCID iD.
* Only stated fields are mapped. An abstract is reconstructed exactly from the
  inverted index or left unknown; nothing is summarised or generated.
* The wrapper adds papers and nothing else. It keeps the inner adapter's
  `contact_authority`, so OpenAlex can never verify or un-verify an address.
* One request per person, sequential, through the existing polite client with
  the deployment contact in `mailto`; a failure fails the refresh (ADR-016).

**Consequences:**

* A person without a stated ORCID iD has no papers until another explicit
  source exists. That gap is shown, not filled.
* A refresh of a large directory makes one OpenAlex request per person with an
  iD. The 30-day faculty TTL and the one-hour failure backoff bound this; a
  per-source rate limiter belongs with the scheduled refresh job.
* Errors in ORCID-to-work attribution inside OpenAlex itself are inherited; the
  per-work re-check guards against filter mismatches, not against a wrong ORCID
  claim in the index.
* One OpenAlex failure (network, HTTP error, invalid JSON) fails the whole
  faculty refresh, as any adapter failure does (ADR-016). The previous rows are
  kept but marked stale, so their official contacts stop being sendable until a
  refresh succeeds. That is the honest state; isolating paper failures from
  profile refreshes needs a separate publication refresh path (QA m2, #25).
* OpenAlex list endpoints return only a work's first 100 authorships
  (`is_authors_truncated`). Such an author list is not copied (authors stay
  unknown), and a person who appears only after the first 100 is not found by
  the per-work re-check, so that work is not attributed. Fetching each large
  work individually would be one extra request per paper; it is deferred
  (QA m1, #25).
* The wrapper refuses to start without a valid contact email, so a request never
  goes out with an empty `mailto` (QA n1, #25).

## ADR-021 — Only verified addresses are displayed; live faculty fetches are CLI-only

**Status:** accepted 2026-11-24 · **Decided by:** Owner (options and recommendation by Dev) · **Spec:** `task.md` §3.5, §8, §13

Phase 5d exposes faculty through the CLI, then the API and screens. Two product
questions came first: how to show an address that is not verified, and who may
trigger a live fetch of faculty pages and OpenAlex.

**Options considered:**

* Unverified address: (a) status plus source link only; (b) the address with an
  "unverified, not sendable" warning; (c) hide the status and link too.
* Live fetch: (a) CLI only for now; (b) the API too, with a rate limit and a
  source allow-list; (c) a scheduled job only.

**Decision (Owner, 2026-11-24):** option (a) for both.

* Every display surface (CLI, API, web) shows an email address only when its
  status is `verified` (`faculty.public_email`). An unverified address is shown
  as `email_status: unverified` plus `email_source_url`, so a student can check
  the evidence but cannot copy the address into an email by hand. The stored
  value is kept for audit and re-verification.
* Live faculty discovery runs only from the operator CLI (`faculty
  --source-url`). The HTTP API and web screens read the cache only, so a student
  request can never trigger a network call to a university or OpenAlex.

**Consequences:**

* A student sees fewer addresses than are stored. That is deliberate (§3.5:
  unverified contacts are excluded from sending).
* A verified address on a **stale** record (past its 30-day TTL) is still shown,
  next to `stale: true` and `contact_sendable: false` (Owner, 2026-11-24, QA m1
  on PR #27). §13.2.4 forbids treating stale data as verified *for outreach*;
  outreach gates on `contact_sendable`, which is false for stale records, so the
  address is orientation only until a refresh re-verifies it.
* `faculty --source-url` requires `--source-institution` (Owner, 2026-11-24, QA
  n1 on PR #27). A page's `worksFor` is often a lab or department, and using it
  as the institution would split one university into several browse nodes.
* Fresh faculty data depends on the operator running the CLI until the scheduled
  refresh worker exists (Phase 10). An API-triggered refresh, if wanted later,
  needs its own ADR with rate limits and a source allow-list.

## ADR-022 — Deterministic, explainable matching with separate eligibility gates

**Status:** accepted 2026-11-24 · **Decided by:** Owner (options and recommendation by Dev) · **Spec:** `task.md` §3.6, §13.4

Phase 6 ranks cached faculty and programs for one student. The spec asks for a
configurable 0-100 score that explains itself, keeps hard eligibility apart
from soft ranking, and names unknowns.

**Options considered and decisions (Owner, 2026-11-24):**

1. Similarity: (a) deterministic keyword rules; (b) an LLM now; (c) embeddings.
   **Chosen: (a).** A score is a pure function of the profile, the cached
   record, the weights and the date, so it is reproducible, free, offline and
   every point can be explained. An LLM layer may be added later behind the §6
   provider interface, as an extra explanation rather than a replacement.
2. Unstated eligibility: (a) `unknown`, flagged and listed as a gap; (b) treat
   as fail; (c) treat as pass. **Chosen: (a).** A gate fails only when the
   source *states* a condition the student does not meet.
3. Default weights: (a) balanced; (b) research-heavy; (c) equal. **Chosen:
   (a)** research interest 35, publication topics 20, skills/coursework 15,
   internship timeline 10, country preference 10, recruitment evidence 5,
   funding 5 (`matching.MatchWeights`, configurable).
4. Storage: (a) a per-user snapshot only when an item is shortlisted; (b) every
   computed score; (c) none. **Chosen: (a)**, implemented in Phase 6b.

**Rules (`matching.py`, `SCORING_VERSION = "1"`):**

* Keyword matching works on normalised phrases (case-folded, punctuation split,
  trailing plural `s` folded). A student term that appears as a whole phrase in
  the source (or contains it) earns full credit; one sharing only a significant
  word (3+ letters, not a stopword) earns half. A factor reaches full credit at
  three matched terms, so listing many keywords is not penalised.
* Factors that do not apply to a target type (timeline and funding for a
  person, publications for a program) are `not_applicable` and leave the
  denominator, so 100 stays reachable. An `unknown` factor earns 0 and stays in
  the denominator: missing evidence lowers the score and is named as a gap.
* Program gates: degree level (a stated `bachelor`/`master`/`phd` compared with
  the student's degree; dual degree fits bachelor and master) and application
  window (stated `closed`, or a stated closing date in the past). Faculty have
  no gates yet (`not_assessed`).
* `rank` sorts every `ineligible` result after all others, whatever its score.
* `inputs_fingerprint` hashes the scoring version, weights, the profile fields
  a score reads and the target's id and last-checked time. Name, email, phone
  and GPA are not inputs. The evaluation date is deliberately left out (6b): a
  stored snapshot is *current* while the fingerprint matches **and** re-scoring
  today gives the same score and eligibility, so a closing date that passes
  overnight still makes a snapshot out of date without every snapshot expiring
  daily.
* Phase 6b storage: `shortlist_items` (one row per student and target, status
  `shortlisted` or `excluded`, UNIQUE for deduplication) and `match_scores`
  (append-only snapshots: score, eligibility, version, fingerprint, date and
  the factors/gates/gaps as JSON). Both carry `user_id` and cascade on account
  deletion; neither is ever read for another student (§13.4).

**Consequences:**

* Synonyms that share no word ("deep learning" vs "neural networks") do not
  match. That is a known limit of option (a) and the reason for a later LLM or
  taxonomy layer.
* A record with little stated data scores low and carries many gaps rather than
  being guessed upward.
