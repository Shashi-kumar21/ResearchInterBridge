# ResearchInterBridge

Local-first assistant that helps students find international research
internships, identify relevant professors and papers, prepare evidence-backed
outreach, and send approved email through connected accounts.

Status: **Phase 1 — foundation**. Research discovery, OAuth and sending are not
implemented yet. Nothing in this repository can send email.

* Product specification: `task.md` (Owner-owned source of truth).
* Team workflow: `AGENTS.md`.
* Current audit and plan: `docs/audit-phase-0.md`.

## Requirements

* Python 3.11+ on CI; the foundation slice also runs on 3.10.11 (see
  `docs/audit-phase-0.md` §7 for the open version decision).
* No third-party runtime dependency yet: configuration, logging, SQLite access
  and the health check use the standard library only.

## Setup (PowerShell)

```powershell
Set-Location "C:\Users\shash\new_project\ResearchInterBridge"
python -m pip install pytest ruff
Copy-Item .env.example .env   # optional; every value has a safe default
```

`.env` is git-ignored. Real environment variables always win over `.env`.
Never put OAuth tokens, client secrets or student documents in `.env`, source
code or commits.

## Run

```powershell
$env:PYTHONPATH = "src"        # or: python -m pip install -e .
python -m research_inter_bridge migrate
python -m research_inter_bridge health
```

`migrate` creates or upgrades the SQLite schema and writes an audit event.
`health` prints a JSON report and exits `1` when the database is unreachable or
the schema is behind, so it can be used as a readiness probe.

## Checks

```powershell
python -m ruff check .
python -m ruff format --check .
python -m pytest
```

Logic that must be verifiable offline lives in `src/research_inter_bridge/`;
tests never call live APIs and never send email.

## Layout

```text
src/research_inter_bridge/    package
  config.py                   environment settings, .env parsing, validation
  logging.py                  mandatory credential redaction
  db.py                       SQLite connection, migrations, audit events
  health.py                   readiness checks
  cli.py / __main__.py        migrate + health commands
tests/                        pytest suite (offline, synthetic data only)
docs/                         audit reports and architecture notes
```

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| `ModuleNotFoundError: research_inter_bridge` | package not on `sys.path` | set `$env:PYTHONPATH = "src"` or `pip install -e .` |
| `configuration error: RIB_LOG_LEVEL must be one of ...` | invalid env value | use `DEBUG`, `INFO`, `WARNING`, `ERROR` or `CRITICAL` |
| `health` exits `1` with `schema_current: false` | database not migrated | run `python -m research_inter_bridge migrate` |
| `health` exits `1` with `filesystem_error` | database path is not writable/valid | point `RIB_DATABASE_PATH` at a writable directory |

Local runtime data (`data/`, `*.db`, `.env`) is git-ignored; keep it that way.
