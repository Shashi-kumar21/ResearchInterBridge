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
