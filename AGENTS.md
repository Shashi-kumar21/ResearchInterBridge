# AGENTS.md

Instructions for every agent working in `ResearchInterBridge`.

## 1. Team

| Role | Who | May do |
| --- | --- | --- |
| **Owner** | Shashi (GitHub `Shashi-kumar21`) | Product decisions, secrets, releases, **only one who merges** |
| **Dev** | opencode session started as agent-bridge agent `dev` | Writes all code, tests, PRs; answers review comments |
| **QA** | opencode session started as agent-bridge agent `qa` (senior) | Reviews, never writes production code, never merges |

Rules:

1. Only Dev pushes code. QA may suggest patches in review comments.
2. Only the Owner merges. Neither agent runs `gh pr merge`.
3. Dev never approves its own PR; QA never merges.
4. Two disagreements -> escalate to the Owner in three lines.
5. Dependency/CI PRs follow the same loop; auto-merge stays off.

## 2. Communication

1. **PR comments** (durable): Dev opens the PR with what/why/verification.
   QA replies with a summary whose first line is exactly one of:

   ```
   VERDICT: APPROVE
   VERDICT: REQUEST_CHANGES
   VERDICT: COMMENT
   ```

   Dev resolves threads only after the fix is pushed and CI is green.
2. **Terminal messages** (fast, via agent-bridge):

   ```bash
   agent-bridge sendmessage qa "PR #12 is up, CI green, ready for review"
   agent-bridge read qa --lines 40
   ```

3. **Owner** only when blocked (merge, secret, product decision).

## 3. Branches

| Branch | Purpose |
| --- | --- |
| `main` | Always releasable; protected, PR + green checks required |
| `dev` | Integration; all work lands here first |
| `feat/*`, `fix/*`, `chore/*` | Short-lived, branch from `dev`, PR back into `dev` |

```
feat/x --PR--> dev --release PR--> main --Owner merges--> tag
```

## 4. Definition of done (before asking QA)

- [ ] `ruff check .`
- [ ] `ruff format --check .`
- [ ] `python -m pytest`
- [ ] New behaviour has tests
- [ ] No `pytest.skip` hiding an untested path
- [ ] No secrets, tokens or personal paths in code or fixtures
- [ ] README updated when behaviour or commands change

## 5. Conventions

* Python >= 3.11, `src/` layout, type hints on public functions.
* Errors: explicit exceptions with actionable messages; no bare `except:`.
* Never log message bodies, tokens or file contents at INFO.
* New dependency needs a justification in the PR.

## 6. Review loop

```bash
# Dev
gh pr create --base dev --head feat/x --title "feat: ..." --body-file .github/pull_request_template.md
gh pr comment <n> --body "Addressed: ..."

# QA
gh pr list
gh pr diff <n>
gh pr review <n> --comment -b "VERDICT: APPROVE"
# (same GitHub account as the PR author, so --approve is rejected; use --comment)

# Owner
gh pr merge <n> --squash --delete-branch
```

## 7. Repository map

```
src/research_inter_bridge/   package
tests/                       pytest suite
.github/workflows/ci.yml     lint + tests on Linux, Windows, macOS
```
