# ResearchInterBridge — web

Next.js + TypeScript site for the public product (`task.md` §5, ADR-010,
ADR-013). It ships a landing page plus register, sign-in and consent screens that
call the Python JSON API.

```powershell
# from the repository root, first
$env:RIB_CORS_ORIGINS = "http://localhost:3000"
py -3.11 -m research_inter_bridge serve

# then
cd web
npm ci
$env:NEXT_PUBLIC_API_BASE_URL = "http://localhost:8000"   # default
npm run dev      # http://localhost:3000
```

`NEXT_PUBLIC_API_BASE_URL` is inlined at build time. Keep the API on
`http://localhost:8000` during development: the session cookie is `SameSite=Lax`,
and `localhost` and `127.0.0.1` are different sites to the browser.

## What the screens do

| Page | Behaviour |
| --- | --- |
| `/register` | Fetches the consent catalogue from the API, shows the exact server texts, and posts email + password + ticked kinds. The submit button stays disabled until the texts load and every required consent is ticked. |
| `/login` | Posts credentials; one sentence covers a wrong password and an unknown email, because the API returns the same body for both. A `429` shows the `Retry-After` wait. |
| `/consent` | Reads the signed-in consent ledger, grants a missing consent, and signs out. Signed out, it links to `/login` and `/register` instead of guessing. |

Everything else on the site is a description of work that does not exist yet. No
page sends email and no page connects Gmail.

## API client

`lib/api.ts` is the only place that talks to the backend:

- `credentials: "include"` on every request. The session cookie is `HttpOnly` and
  host-scoped to the API; no token is ever readable by JavaScript.
- Failures become `ApiError` with the API's stable `code`, the HTTP status and,
  for `429`, the `Retry-After` seconds. Forms branch on the code, never on
  message text. The API exposes `Retry-After` through CORS for this reason.
- `lib/errors.ts` maps codes to one actionable sentence (`email_taken`,
  `invalid_credentials`, `account_locked`, `rate_limited`, `invalid_session`, ...).

Consent texts are never copied into the site. A page that failed to load them
cannot submit, so nobody can "agree" to text that was not shown.

## Tests

```powershell
npm run lint
npm run typecheck
npm test          # vitest
npm run build
```

- `lib/*.test.ts` — roadmap invariants, API client (mocked `fetch`), error copy.
- `components/*.test.tsx` — the three forms under jsdom with Testing Library:
  consent gating, posted bodies, duplicate-email message, lockout `Retry-After`,
  401 handling, grant-then-refresh, sign-out.

Dev-only dependencies added with the screens: `@testing-library/react`,
`@testing-library/user-event`, `@testing-library/jest-dom` and `jsdom`. They are
not shipped to the browser; they exist so form behaviour has the same kind of
regression test as the Python side, instead of being checked by hand.

## Honesty guards

All product copy that could over-claim lives in `lib/roadmap.ts` as typed data:

- `CAPABILITIES` — `status` (`available` / `in-progress` / `planned`) and
  `surface` (`cli` / `api` / `web`).
- `LIMITS` — free-for-students, provider quotas, Gmail-sends-not-us, no bulk.
- `WORKFLOW` — the five steps of the finished product.

`lib/roadmap.test.ts` fails the build if:

- a capability is missing a title or a detail;
- status or surface is not a known value;
- email sending is ever labelled `available`;
- a capability marked `available` names a surface it does not really run on
  (CLI, HTTP API or this website), or its "available on" line disagrees with its
  status badge;
- the web canary is wrong: the shipped account screens must be the `web` card,
  and the predicate must still reject a fabricated one;
- the account copy claims more than export can do (it says "account data", not
  "everything you uploaded");
- the counts stop matching the list.

Prose written directly in `page.tsx` is invisible to these tests, so keep claims
in the data.

## Deliberate constraints

- **No external fonts, images or analytics.** Pages render offline, and the build
  makes no network request.
- **No UI framework.** Plain CSS with design tokens in `app/globals.css`; the only
  client components are the three account forms.
- **Consent text comes from the server.** A local copy would drift from the text
  recorded against a policy version.
- **Security headers** (`nosniff`, `DENY` framing, referrer policy, permissions
  policy, CSP subset — `base-uri`, `object-src`, `frame-ancestors`,
  `form-action`) are set in `next.config.ts`. `script-src` still needs a
  per-request nonce because Next emits inline bootstrap scripts; the forms
  submit with JavaScript, and with JavaScript disabled they simply cannot submit
  (nothing is silently posted somewhere else).
- `react/no-danger` is an ESLint error: third-party text must never be injected
  as HTML. It does not cover `href`/`src`, so later phases validate links.
- ESLint is pinned to `^9.39` because `eslint-plugin-react` (via
  `eslint-config-next`) does not declare ESLint 10 support yet. Pinning keeps
  `npm ci` deterministic.
- Plain `<a>` is used for internal navigation inside the client forms so they
  render in tests without the App Router; the pages are static and the Next
  `Link` component is used everywhere else.

## Status

Landing page, register, sign in and consent. Next: onboarding, profile and
document upload (2d-4), then discovery and the email studio. The Gmail connection
arrives in Phase 9; until then no email can be sent from this site.
