# ResearchInterBridge

Local-first assistant that helps students find international research
internships, identify relevant professors and papers, prepare evidence-backed
outreach, and send approved email through connected accounts.

Status: **Phase 2 — auth, profile, documents and the web shell**. Accounts,
consent, sessions, profiles, document ingestion (PDF/DOCX/TXT) and candidate-fact
extraction work from the CLI; the Next.js landing page is live in `web/`.
Research discovery, OAuth and sending are not implemented yet, and nothing in
this repository can send email.

* Product specification: `task.md` (Owner-owned source of truth).
* Team workflow: `AGENTS.md`.
* Decisions (Python floor, backend-first, redaction): `docs/decisions.md`.
* Current audit and plan: `docs/audit-phase-0.md`.

## Requirements

* **Python 3.11+** (see `docs/decisions.md` ADR-001). On this machine the
  default `python` is 3.10.11, so use `py -3.11` for the suite.
* Runtime dependencies: `pymupdf` (PDF text) and `python-docx` (DOCX text),
  installed with `pip install -e .`. Configuration, logging, SQLite access and
  the health check still use the standard library only.

## Setup (PowerShell)

```powershell
Set-Location "C:\Users\shash\new_project\ResearchInterBridge"
py -3.11 -m pip install -e . pytest
python -m pip install ruff        # Ruff is a standalone binary; any Python works
Copy-Item .env.example .env       # optional; every value has a safe default
```

`.env` is git-ignored. Real environment variables always win over `.env`.
Never put OAuth tokens, client secrets or student documents in `.env`, source
code or commits.

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

## Checks

```powershell
python -m ruff check .
python -m ruff format --check .
py -3.11 -m pytest

cd web
npm ci
npm run lint; npm run typecheck; npm test; npm run build
```

Logic that must be verifiable offline lives in `src/research_inter_bridge/`;
tests never call live APIs and never send email.

## Frontend (`web/`)

Next.js + TypeScript (ADR-010), currently one static, accessible landing page.

```powershell
cd web
npm ci
npm run dev      # http://localhost:3000
```

The page carries no sign-up form yet because there is no HTTP API to submit to.
Product copy lives in `web/lib/roadmap.ts` and `web/lib/roadmap.test.ts` enforces
it: a capability cannot be labelled `available` on the `web` surface, email
sending cannot be `available`, and every entry needs a real detail. There are no
external fonts, images, analytics or client-side JS, so the build is offline and
self-hosting stays predictable. `web/README.md` explains the constraints.

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
  cli.py / __main__.py        migrate, health, auth, consent, document and fact commands
tests/                        pytest suite (offline, synthetic data only)
web/                          Next.js + TypeScript site (landing page today)
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
| `login` fails with "too many failed attempts" | five wrong passwords within 15 minutes | wait for the lockout window or use the account owner's reset flow |

Local runtime data (`data/`, `*.db`, `.env`) is git-ignored; keep it that way.
