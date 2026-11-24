# Audit notes per slice

Each PR adds **one new file** here, named after its slice (for example
`6-perf.md`). It holds the reviewer notes that used to be appended to
`docs/audit-phase-0.md`: what changed, the rules it enforces, the decisions it
follows and the tests that cover it.

Feature PRs do not edit the delivery-log table in `docs/audit-phase-0.md`.
After the Owner merges, Dev marks the slice as merged in a small `docs/*` PR.
This keeps parallel PRs from editing the same lines (agreed with QA,
2026-11-24).
