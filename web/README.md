# ResearchInterBridge — web

Next.js + TypeScript shell for the public site (`task.md` §5, ADR-010).

This slice ships one honest landing page. The backend is still CLI-only, so the
page describes what the repository can actually do and labels the rest as
planned; it contains no sign-up form, because a form that cannot submit is worse
than no form.

## Commands

```powershell
cd web
npm ci                 # or: npm install, first time
npm run dev            # http://localhost:3000
npm run lint
npm run typecheck
npm test
npm run build
```

CI runs lint → typecheck → test → build in the additive `frontend` job.

## How the page stays honest

All product copy that could over-claim lives in `lib/roadmap.ts` as typed data:

- `CAPABILITIES` — what exists, with `status` (`available` / `in-progress` /
  `planned`) and `surface` (`cli` / `web`).
- `LIMITS` — free-for-students, provider quotas, Gmail-sends-not-us, no bulk.
- `WORKFLOW` — the five steps of the finished product.

`lib/roadmap.test.ts` fails the build if:

- a capability is missing a title or a detail;
- status or surface is not a known value;
- email sending is ever labelled `available`;
- any `available` capability claims the `web` surface (a canary that must be
  flipped deliberately with the first real web feature);
- the counts stop matching the list.

So shipping a screen means changing data, not adding marketing prose that drifts
from the code.

## Deliberate constraints

- **No external fonts, images or analytics.** The page renders offline and the
  build makes no network request, which keeps CI and self-hosting predictable.
- **No UI framework.** Plain CSS with design tokens in `app/globals.css`; fewer
  dependencies to audit for a product that will hold CVs.
- **Server components only.** No client JavaScript is shipped for static copy.
- **Security headers** (`nosniff`, `DENY` framing, referrer policy, permissions
  policy) are set in `next.config.ts`. Later phases that render scraped
  professor/paper text need a CSP as well.
- `react/no-danger` is an ESLint error: untrusted third-party text must never be
  injected as HTML.

## Status

Landing page only. Registration, login, onboarding, upload and fact confirmation
arrive with the HTTP API (next slices), then discovery and the email studio.
