# Architecture decisions

Short, dated decisions that unblock agents and are cheap to revisit. Any change
here needs the Owner's agreement; the reasoning is recorded so the next agent
does not re-litigate it.

## ADR-001 — Python floor is 3.11 (supersedes `task.md` §0)

**Status:** accepted 2026-10-30 · **Decided by:** Owner (delegated to Dev)

`task.md` §0 says "Python compatibility: Python 3.10.x", while `pyproject.toml`
(`requires-python = ">=3.11"`, Ruff `target-version = "py311"`), `AGENTS.md` §5
("Python >= 3.11") and CI (3.12) all say 3.11+.

**Decision:** the floor is **3.11**; `task.md` §0 is superseded. Python 3.10 left
security support in October 2025, and this product stores OAuth tokens and
student documents, so an EOL interpreter is not acceptable.

**Consequences:** `datetime.UTC` is used instead of the `timezone.utc` workaround;
CI also runs the suite on 3.11 (`test-python311`, additive check — the four
required check names are untouched); local commands use `py -3.11` because the
machine's default `python` is 3.10.11.

## ADR-002 — Backend-first until Phase 10

**Status:** accepted 2026-10-30

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
answer do not exist yet. Phase 7 will therefore be built and tested against
mocked provider adapters first; live connect/disconnect needs
`RIB_GOOGLE_OAUTH_CLIENT_ID` and `RIB_GOOGLE_OAUTH_CLIENT_SECRET` in `.env`.
No credential will ever be committed, logged or shared through the bridge.
