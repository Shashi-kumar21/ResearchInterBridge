# ResearchInterBridge

Local-first assistant that helps students find international research
internships, identify relevant professors and papers, prepare evidence-backed
outreach, and send approved email through connected accounts.

Status: **Phase 1 — foundation**. Research discovery, OAuth and sending are not
implemented yet. Nothing in this repository can send email.

* Product specification: `task.md` (Owner-owned source of truth).
* Team workflow: `AGENTS.md`.
* Decisions (Python floor, backend-first, redaction): `docs/decisions.md`.
* Current audit and plan: `docs/audit-phase-0.md`.

## Requirements

* **Python 3.11+** (see `docs/decisions.md` ADR-001). On this machine the
  default `python` is 3.10.11, so use `py -3.11` for the suite.
* No third-party runtime dependency yet: configuration, logging, SQLite access
  and the health check use the standard library only. CI needs `pytest` only.

## Setup (PowerShell)

```powershell
Set-Location "C:\Users\shash\new_project\ResearchInterBridge"
py -3.11 -m pip install pytest
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
```

`migrate` creates or upgrades the SQLite schema and writes an audit event.
`health` prints a JSON report and exits `1` when the database is missing,
unreadable or behind on migrations, so it can be used as a readiness probe. The
probe opens the database **read-only**: a health check never creates tables or
changes the file.

## Checks

```powershell
python -m ruff check .
python -m ruff format --check .
py -3.11 -m pytest
```

Logic that must be verifiable offline lives in `src/research_inter_bridge/`;
tests never call live APIs and never send email.

## Layout

```text
src/research_inter_bridge/    package
  config.py                   environment settings, .env parsing, validation
  logging.py                  mandatory credential redaction (filter + formatter)
  db.py                       SQLite connection, read-only probe, migrations, audit
  health.py                   read-only readiness checks
  cli.py / __main__.py        migrate + health commands
tests/                        pytest suite (offline, synthetic data only)
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

Local runtime data (`data/`, `*.db`, `.env`) is git-ignored; keep it that way.
