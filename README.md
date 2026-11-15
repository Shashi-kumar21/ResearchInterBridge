# ResearchInterBridge

Local-first assistant that helps students find international research
internships, identify relevant professors and papers, prepare evidence-backed
outreach, and send approved email through connected accounts.

Status: **Phase 4 — auth, profile, documents, research cache and the web
screens**. Accounts, consent, sessions, profiles, document ingestion
(PDF/DOCX/TXT), candidate-fact confirmation, apply-to-profile and cache-only
research browsing work from the CLI and the HTTP API, and the Next.js site in
`web/` can register, sign in, edit the profile, upload a document, review facts
and browse cached institutions, departments and programs. Discovery has a polite
HTTP and explicit-page parsing boundary (ADR-017) and the CLI can read configured
official pages live; the HTTP API still exposes cache-only browse reads. OAuth
and sending are not implemented, and nothing in this repository can send email.

* Product specification: `task.md` (Owner-owned source of truth).
* License: MIT (see `LICENSE`), decided by the Owner on 2026-11-02.
* Team workflow: `AGENTS.md`.
* Decisions (Python floor, backend-first, redaction): `docs/decisions.md`.
* Current audit and plan: `docs/audit-phase-0.md`.

## Requirements

* **Python 3.11+** (see `docs/decisions.md` ADR-001). On this machine the
  default `python` is 3.10.11, so use `py -3.11` for the suite.
* Runtime dependencies: `pymupdf` (PDF text), `python-docx` (DOCX text),
  `fastapi` and `uvicorn` (JSON API), `python-multipart` (multipart uploads),
  installed with `pip install -e .`. Configuration, logging, SQLite access and
  the health check still use the standard library only. `.[test]` adds `pytest`
  and `httpx` for the API tests.

## Setup (PowerShell)

```powershell
Set-Location "C:\Users\shash\new_project\ResearchInterBridge"
py -3.11 -m pip install -e ".[test]"
python -m pip install ruff        # Ruff is a standalone binary; any Python works
Copy-Item .env.example .env       # optional; every value has a safe default
```

`.env` is git-ignored. Real environment variables always win over `.env`.
Never put OAuth tokens, client secrets or student documents in `.env`, source
code or commits.

A live research fetch needs a reachable operator address:
`RIB_RESEARCH_CONTACT_EMAIL` is sent in the `User-Agent` and `From` headers
(ADR-017). It is blank by default, so a fresh checkout cannot contact a source;
offline browsing and the parser tests still work. It must be an operational
address, never a student's.

## Run

```powershell
$env:PYTHONPATH = "src"           # or: py -3.11 -m pip install -e .
py -3.11 -m research_inter_bridge migrate
py -3.11 -m research_inter_bridge health
py -3.11 -m research_inter_bridge register --email you@example.edu
py -3.11 -m research_inter_bridge login --email you@example.edu
py -3.11 -m research_inter_bridge whoami
py -3.11 -m research_inter_bridge consent-status --user-id 1
py -3.11 -m research_inter_bridge document-add --user-id 1 --file .\cv.pdf --parse
py -3.11 -m research_inter_bridge document-list --user-id 1
py -3.11 -m research_inter_bridge fact-list --user-id 1
py -3.11 -m research_inter_bridge fact-confirm --user-id 1 --fact-id 3 --value "9.1/10"
py -3.11 -m research_inter_bridge fact-apply --user-id 1
py -3.11 -m research_inter_bridge discover --offline --country Germany
$env:RIB_RESEARCH_CONTACT_EMAIL = "research@example.org"
py -3.11 -m research_inter_bridge discover --source-url https://example.edu/programs --country Germany
py -3.11 -m research_inter_bridge logout
```

`migrate` creates or upgrades the SQLite schema and writes an audit event.
`health` prints a JSON report and exits `1` when the database is missing,
unreadable or behind on migrations, so it can be used as a readiness probe. The
probe opens the database **read-only**: a health check never creates tables or
changes the file.

`register` creates an account and records the consents the student accepted
(`personal_data` and `document_processing` by default; pass `--consent` to narrow
them and registration refuses rather than assuming). Before anything is written
the exact consent text and policy version are printed, so the ledger entry is
informed rather than implied; stdout stays machine-readable JSON for
`--password-stdin` automation. Passwords are prompted with `getpass`, never
taken as an argument; `--password-stdin` exists for automation only. Only a
salted `scrypt` hash is stored, and the password policy is length based
(10–200 characters) plus a short blocklist. Five wrong passwords lock the
account for 15 minutes; once that window elapses the attempt counter starts
fresh (a successful login or a password change clears it too), so a single typo
never re-locks an account.

`login` stores a session token in `data/session` (created with mode `0600` where
supported), `whoami` validates it and `logout` revokes it and deletes the file.
Only a SHA-256 of the token is kept in the database, so a database leak cannot
be replayed as a live session. Sessions expire after 12 hours; changing the
password revokes every existing session.

`consent-accept` records a consent kind (`personal_data`, `document_processing`,
`gmail_access`) against the current policy version, and `consent-status` prints
the ledger. Consent is versioned: bumping `POLICY_VERSION` in `consent.py` makes
the product ask again instead of reusing an old acceptance. `document-add`
refuses to store or parse anything until `document_processing` is recorded
(task.md §3.1).

`document-add` validates the real content (not the extension), stores the file
under `data/documents/user_<id>/<hash>.<ext>` with a `0600` mode where the OS
supports it, and `--parse` extracts text plus candidate facts. Extracted facts are
`pending` until the student confirms them; only `confirmed` facts may ever appear
in an email. Scanned PDFs are rejected with an "OCR not available yet" message
rather than silently producing nothing.

`fact-list` groups candidates per field and marks `has_conflict: true` when two
documents disagree, so the student picks the right value instead of one silently
winning. `fact-confirm` accepts a correction (`--value`), and confirming one
candidate rejects the competing pending ones for that field. `fact-apply` copies
confirmed facts into the profile: blank fields are filled, values the student
typed are kept unless `--overwrite` is passed. Graduation **year** facts are
never turned into a date, because that would invent a day and month.

`discover` reads the shared research cache (`task.md` §13, ADR-016). Each program
is printed with its official URL, source URLs, `last_checked_at` and a `stale`
flag; unknown deadlines, funding or degree level stay `null` instead of being
guessed. The cache holds public research data only and is shared across students.
`--offline` browses what is cached and matches the filters against the local
cache, so a filter works even when that exact query never reached a source. An
online query without a source exits `1` with an actionable message. A fresh
query makes no network call; a stale entry is refreshed on demand, and a failed
refresh keeps the previous record marked stale and is not retried for an hour.

Live discovery is opt-in per command:
`--source-url` names one official program page (repeat for more), and
`--source-institution` supplies the institution when the page does not state a
provider. It needs `RIB_RESEARCH_CONTACT_EMAIL`; without it the command exits
before any request. `--offline` and `--source-url` cannot be combined.

The live-source boundary is `http_source.py` plus `program_pages.py` (ADR-017).
It performs one GET per configured page with a contact-bearing `User-Agent`, a
timeout, a 2 MiB cap and an HTTP(S)-only rule, then accepts only schema.org
JSON-LD typed `EducationalOccupationalProgram` or `JobPosting`. A field the page
does not state stays `None`; prose deadlines are not parsed. Parsed records pass
through the same filter rule as the cache before they are stored, so a live
result and an offline result cannot disagree. The HTTP API does not expose this
live path yet; its `/api/research/*` routes remain cache-only.

## HTTP API (`serve`)

```powershell
py -3.11 -m research_inter_bridge serve          # http://127.0.0.1:8000
py -3.11 -m research_inter_bridge serve --port 8123
```

The API is a thin layer over the same services the CLI uses, so no rule is
implemented twice. It applies pending migrations at startup and refuses to start
if the database cannot be opened.

| Method | Path | Auth | Purpose |
| --- | --- | --- | --- |
| `GET` | `/api/health` | none | read-only readiness probe; `503` when not migrated |
| `GET` | `/api/consents/catalogue` | none | consent texts + policy version for the registration screen |
| `POST` | `/api/auth/register` | none | create an account, record consent, sign in |
| `POST` | `/api/auth/login` | none | verify credentials, set the session cookie |
| `POST` | `/api/auth/logout` | cookie | revoke the session, clear the cookie (idempotent) |
| `GET` | `/api/auth/me` | cookie | account details and consent state |
| `GET` | `/api/auth/export` | cookie | everything stored for this account (no hashes or tokens) |
| `DELETE` | `/api/auth/account` | cookie + password | delete the account and all of its data |
| `GET` | `/api/consents` | cookie | consent state |
| `POST` | `/api/consents` | cookie | record one consent (idempotent) |
| `GET` | `/api/profile` | cookie | profile and completeness; `404 profile_missing` before onboarding |
| `PUT` | `/api/profile` | cookie | create or replace the profile (full replacement) |
| `GET` | `/api/documents` | cookie | list stored documents, newest first |
| `POST` | `/api/documents` | cookie + multipart | validate and store one PDF/DOCX/TXT (`file`, `kind`) |
| `GET` | `/api/documents/{id}` | cookie | one document's metadata (never its bytes or path) |
| `POST` | `/api/documents/{id}/parse` | cookie | extract text and pending fact candidates (idempotent) |
| `DELETE` | `/api/documents/{id}` | cookie | delete the row and stored file |
| `GET` | `/api/facts` | cookie | fact candidates grouped by field, conflicts flagged |
| `PATCH` | `/api/facts/{id}` | cookie | confirm (optional correction) or reject one candidate |
| `POST` | `/api/facts/apply` | cookie | copy confirmed facts into blank profile fields |
| `GET` | `/api/research/programs` | cookie | browse cached programs (`area`, `country`, `department`, `degree_level`, `institution_id`, `department_id`, `limit`, `offset`) |
| `GET` | `/api/research/programs/{id}` | cookie | one cached program; `404 program_missing` |
| `GET` | `/api/research/institutions` | cookie | cached institutions with program/department counts (`country`, paging) |
| `GET` | `/api/research/institutions/{id}/departments` | cookie | cached departments of one institution; `404 institution_missing` |

Every failure has the same shape, `{"error": {"code", "message"}}`, with stable
codes such as `invalid_credentials` (401), `account_locked` (429), `email_taken`
(409), `invalid_session` (401), `consent_required` (403), `invalid_request`
(422), `rate_limited` (429), `deletion_incomplete` (409), `profile_missing`
(404), `fact_missing` (404), `invalid_profile` (400), `document_missing` (404),
`document_too_large` (413), `unsupported_document` (415),
`document_unreadable` (422), `program_missing` (404), `institution_missing`
(404) and `discovery_unavailable` (503). A rejected request body never echoes
its values, so a bad password is not reflected back or written to the caller's
logs.

The `/api/research/*` endpoints are cache-only reads: they never call a source,
so they work with discovery offline, and a session is still required. Every row
carries `last_checked_at` and `stale`; a stale row may be shown for orientation
but must not be treated as verified for outreach or deadline decisions. Filters
match the same way as `discover --offline`, and an unknown (NULL) stored value
never matches a stated filter.

Documents are capped at 10 MiB and must be a real PDF, DOCX or plain-text file;
the extension and the sniffed content must agree. Uploading identical bytes twice
returns the existing record instead of storing a second copy. Storing or parsing
a document requires the current `document_processing` consent. Profile,
document and fact responses are `Cache-Control: no-store`.

`register` and `login` are rate-limited per transport peer address before any
password hashing happens: 30 registrations per hour and 120 logins per 15
minutes. A refused request answers `429` with `rate_limited` and a `Retry-After`
header (seconds). The account lockout uses the same status and header but the
`account_locked` code. Counters live in SQLite (`rate_limit_counters`), so a
restart does not reset them, and the stored subject is a SHA-256 of the address,
never the address itself. The key is the transport peer (`request.client`),
not the raw `X-Forwarded-For` header. Behind a reverse proxy that is only safe
when the server trusts *only* that proxy: with uvicorn, `--proxy-headers` makes
`request.client` come from `X-Forwarded-For`, so `--forwarded-allow-ips` must
list the proxy addresses and never `*`.

Sessions live in an `HttpOnly`, `SameSite=Lax`, host-only cookie
(`RIB_SESSION_COOKIE_NAME`, default `rib_session`); only a SHA-256 of the token is
stored server-side. Set `RIB_COOKIE_SECURE=true` when the API is served over
HTTPS. Cross-origin callers must be listed exactly in `RIB_CORS_ORIGINS` (no
wildcards; values are stored lowercase); the default trusts none. In addition,
an unsafe method that carries an `Origin` header is rejected unless that origin
is exactly the request's own (scheme, host and port) or an allow-listed one, and
the check runs before authentication. Behind a TLS-terminating proxy the app
must be told the original scheme (for uvicorn: `--proxy-headers` plus
`--forwarded-allow-ips`), otherwise `https` browser origins are refused.

For frontend development, run the API and the site on different ports and set
`RIB_CORS_ORIGINS=http://localhost:3000` (ports do not affect `SameSite`, so the
cookie is still sent). For automatic reload during development:

```powershell
uvicorn research_inter_bridge.api:create_app --factory --reload --port 8000
```

Not implemented yet: live discovery through the HTTP API, OAuth and email
sending.

## Checks

```powershell
python -m ruff check .
python -m ruff format --check .
py -3.11 -m pytest

cd web
npm ci
npm run lint; npm run typecheck; npm test; npm run build
```

The `frontend` CI job runs the same four steps on Node 24 with the committed
`web/package-lock.json`.

Logic that must be verifiable offline lives in `src/research_inter_bridge/`;
tests never call live APIs and never send email.

## Frontend (`web/`)

Next.js + TypeScript (ADR-010/ADR-013/ADR-015/ADR-016): the landing page plus
register, sign in, consent, onboarding/profile, documents, fact-review and
research-browse screens that talk to the JSON API. The research screen reads
`/api/research/*` only, so it cannot contact a source: it drills from
institution to department to program, keeps each row's source link and
last-checked date, and labels a stale row "orientation only".

```powershell
# terminal 1: API with the site's origin allowed and a session cookie
$env:RIB_CORS_ORIGINS = "http://localhost:3000"
py -3.11 -m research_inter_bridge serve

# terminal 2: the site
cd web
npm ci
$env:NEXT_PUBLIC_API_BASE_URL = "http://localhost:8000"   # also the default
npm run dev      # http://localhost:3000
```

`NEXT_PUBLIC_API_BASE_URL` is read at build time, so a production build needs it
set before `npm run build`. Use `http://localhost:8000` (not `127.0.0.1`) while
developing: the session cookie is `SameSite=Lax`, and a different host would make
the API a different site.

The consent texts on the registration form are fetched from
`GET /api/consents/catalogue`, never copied into the page, so the text a student
agrees to matches the policy version the server records. Product copy lives in
`web/lib/roadmap.ts` and `web/lib/roadmap.test.ts` enforces its shape: a card's
surface line must agree with its badge, email sending cannot be `available`, and
only capabilities that really run in a browser may claim the `web` surface. The
content screens read and write server state, never a browser copy: a missing
profile is a new form, a failed parse is shown with the server's reason, and a
fact confirmation refreshes the whole field because one choice can reject
competing candidates. There are no hand-written analytics, external fonts or
images, and the build itself makes no network request. Submit buttons stay
disabled until React hydrates, every form declares `method="post"` and the CSP
sets `form-action 'none'`, so a pre-hydration click cannot submit natively.
Tested on Node 24 (`web/.nvmrc`); `web/README.md` explains the constraints.

## Layout

```text
src/research_inter_bridge/    Python package
  config.py                   environment settings, .env parsing, validation
  logging.py                  mandatory credential redaction (filter + formatter)
  db.py                       SQLite connection, read-only probe, migrations, audit
  health.py                   read-only readiness checks
  auth.py                     accounts, scrypt hashes, sessions, lockout, export/delete
  consent.py                  consent texts, policy version, consent ledger
  profiles.py                 student profile model, validation, per-user storage, facts
  documents.py                upload validation, hash-named storage, per-user isolation
  parsing.py                  PDF/DOCX/TXT text extraction with bounded limits
  facts.py                    fact extraction + confirm/reject/conflict workflow
  discovery.py                source-agnostic program cache, filters, browse reads
  http_source.py              polite contact-bearing HTTP GET boundary (ADR-017)
  program_pages.py            explicit schema.org JSON-LD program parser
  api/                        FastAPI app: sessions, CORS, consent and account routes
  cli.py / __main__.py        migrate, health, serve, auth, consent, document and fact commands
tests/                        pytest suite (offline, synthetic data only)
web/                          Next.js + TypeScript site (landing, account, content and research screens)
web/lib/api.ts                typed browser client for the JSON API
web/lib/roadmap.ts            capability/limit/workflow data the page renders
docs/                         audit report, decisions, architecture notes
```

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| `ModuleNotFoundError: research_inter_bridge` | package not on `sys.path` | set `$env:PYTHONPATH = "src"` or `py -3.11 -m pip install -e .` |
| `import datetime.UTC` / `ImportError` | ran the suite on Python 3.10 | use `py -3.11 -m pytest` |
| `configuration error: RIB_LOG_LEVEL must be one of ...` | invalid env value | use `DEBUG`, `INFO`, `WARNING`, `ERROR` or `CRITICAL` |
| `health` exits `1` with `schema_current: false` | database not migrated | run `py -3.11 -m research_inter_bridge migrate` |
| `health` exits `1` with `database_error: ... does not exist` | database not created yet | run `migrate` first; the probe never creates it |
| `document-add` fails with "requires the 'document_processing' consent" | consent not recorded for that user | run `consent-accept --user-id <id> --kind document_processing` |
| `whoami` fails with "not signed in" | no `data/session` file | run `login --email ...` |
| `login` fails with "too many failed attempts" | five wrong passwords within 15 minutes | wait for the lockout window |
| API browser call fails with a CORS error | origin not listed | add the exact origin (e.g. `http://localhost:3000`) to `RIB_CORS_ORIGINS` and restart |
| API call returns `403 origin_not_allowed` | cross-origin `POST` from an origin that is not allow-listed | fix `RIB_CORS_ORIGINS`; same-site requests through one origin need no entry |

Local runtime data (`data/`, `*.db`, `.env`) is git-ignored; keep it that way.
