# ResearchInterBridge — web

Next.js + TypeScript shell for the public site (`task.md` §5, ADR-010).

This slice ships one honest landing page. The backend is still CLI-only, so the
page describes what the repository can actually do and labels the rest as
planned; it contains no sign-up form, because a form that cannot submit is worse
than no form.

## Commands

```powershell
cd web
npm ci                 # or: npm install, first time (Node 24, see .nvmrc)
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
  flipped deliberately with the first real web feature), including the positive
  case that proves the predicate is not dead code;
- a card's "available on" line disagrees with its status badge — `surfaceLabel()`
  is pure and unit-tested, so "Planned · Available on: the website" cannot ship;
- the counts stop matching the list.

What these tests cannot see is prose written directly in `page.tsx`. That is why
the copy lives in `roadmap.ts`; if you add a sentence to JSX, no test will catch
an over-claim. Keep claims in the data.

## Deliberate constraints

- **No external fonts, images or analytics.** The page renders offline and the
  build makes no network request, which keeps CI and self-hosting predictable.
- **No UI framework.** Plain CSS with design tokens in `app/globals.css`; fewer
  dependencies to audit for a product that will hold CVs.
- **Server components only.** No client components and no hand-written browser
  JavaScript; Next still ships its framework runtime (measured: ~171 KB gzipped,
  6 script tags for this page). The claim is "no app code in the browser", not
  "zero JavaScript".
- **Security headers** (`nosniff`, `DENY` framing, referrer policy, permissions
  policy, and a CSP directive subset — `base-uri`, `object-src`,
  `frame-ancestors`, `form-action`) are set in `next.config.ts`. `script-src`
  needs a per-request nonce because Next emits inline bootstrap scripts; that
  arrives with the dynamic pages, not as a blanket `'unsafe-inline'`.
- `react/no-danger` is an ESLint error: untrusted third-party text must never be
  injected as HTML. It does not cover `href`/`src` pointing at untrusted URLs, so
  later phases validate links separately.
- ESLint is pinned to `^9.39` because `eslint-plugin-react` (via
  `eslint-config-next`) does not declare ESLint 10 support yet; pinning keeps
  `npm ci` deterministic instead of relying on a peer-resolution warning. Move
  to 10 when the plugin declares it.

## Status

Landing page only. Registration, login, onboarding, upload and fact confirmation
arrive with the HTTP API (next slices), then discovery and the email studio.
