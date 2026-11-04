# ResearchInterBridge — web

Next.js + TypeScript site for the public product (`task.md` §5, ADR-010,
ADR-013, ADR-015). It ships a landing page plus register, sign-in, consent,
profile, document and fact-review screens that call the Python JSON API.

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

Node: `package.json` declares the real floor, `^22.13.0 || ^24.0.0 || >=26.0.0`
(vitest and jsdom do not support older Node); `.nvmrc` and CI use Node 24.

## What the screens do

| Page | Behaviour |
| --- | --- |
| `/register` | Fetches the consent catalogue from the API, shows the exact server texts, and posts email + password + ticked kinds. The submit button stays disabled until the texts load and every required consent is ticked. |
| `/login` | Posts credentials; one sentence covers a wrong password and an unknown email, because the API returns the same body for both. A `429` shows the `Retry-After` wait. |
| `/consent` | Reads the signed-in consent ledger, grants a missing consent, and signs out. Signed out, it links to `/login` and `/register` instead of guessing. |
| `/onboarding` | Loads the profile, treats `404 profile_missing` as a new form, saves the whole profile with `PUT`, and shows the server's completeness percentage and missing labels. |
| `/documents` | Lists documents, uploads multipart PDF/DOCX/TXT, extracts facts, and deletes after a confirmation. A failed parse is kept and shown with the server's reason. |
| `/facts` | Groups candidates by field, marks conflicts, confirms (with an optional correction) or rejects one candidate, and applies confirmed contact email/phone/GPA to the profile. |

Discovery, matching, CV review, email drafting and sending are still
descriptions of work that does not exist yet. No page sends email and no page
connects Gmail.

## API client

`lib/api.ts` is the only place that talks to the backend:

- `credentials: "include"` on every request. The session cookie is `HttpOnly` and
  host-scoped to the API; no token is ever readable by JavaScript.
- Failures become `ApiError` with the API's stable `code`, the HTTP status and,
  for `429`, the `Retry-After` seconds. Forms branch on the code, never on
  message text. The API exposes `Retry-After` through CORS for this reason.
- `PUT` and `PATCH` carry JSON; an upload is `FormData` and deliberately does not
  set `Content-Type`, because the browser must add the multipart boundary.
- `lib/errors.ts` maps codes to one actionable sentence (`email_taken`,
  `invalid_credentials`, `account_locked`, `rate_limited`, `invalid_session`,
  `profile_missing`, `document_too_large`, `unsupported_document`,
  `document_unreadable`, `deletion_incomplete`, ...).

Consent texts are never copied into the site. A page that failed to load them
cannot submit, so nobody can "agree" to text that was not shown.

## Tests

```powershell
npm run lint
npm run typecheck
npm test          # vitest
npm run build
```

- `lib/*.test.ts` — roadmap invariants, the API client (mocked `fetch`: JSON,
  `PUT`/`PATCH`, 204, multipart upload without a JSON type, coded errors), error
  copy, byte/date formatting, security headers (`form-action 'none'`).
- `components/*.test.tsx` — all six interactive screens under jsdom with Testing
  Library: consent gating, posted bodies, duplicate-email message, lockout
  `Retry-After`, 401 handling, grant-then-refresh, sign-out, profile
  load/new/save/replace semantics, upload/parse/delete, failed parse reasons,
  locked-file deletion, conflict display, confirm/reject/correct, apply with
  overwrite, and server-rendered HTML proving the sign-in/register submit buttons
  cannot fire natively before hydration.

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
- the web canary is wrong: the shipped screens must be the `web` card, its copy
  must not say they are still to come, and the predicate must still reject a
  fabricated one;
- the account copy claims more than export can do (it says "account data", not
  "everything you uploaded");
- the counts stop matching the list.

Prose written directly in `page.tsx` is invisible to these tests, so keep claims
in the data.

## Deliberate constraints

- **No external fonts, images or analytics.** Pages render offline, and the build
  makes no network request.
- **No UI framework.** Plain CSS with design tokens in `app/globals.css`; the
  only client components are the six screens that need a session (register,
  sign in, consent, profile, documents, facts). Everything else is server-rendered.
- **No native form submission.** The forms declare `method="post"`, their submit
  buttons stay disabled until React has hydrated (`lib/hydration.ts`), and the
  CSP sets `form-action 'none'`. A click before hydration or with JavaScript
  disabled cannot fall back to a GET that puts a password in the URL; a
  `<noscript>` note says JavaScript is needed.
- **Consent text comes from the server.** A local copy would drift from the text
  recorded against a policy version.
- **Security headers** (`nosniff`, `DENY` framing, referrer policy, permissions
  policy, CSP subset — `base-uri`, `object-src`, `frame-ancestors`,
  `form-action 'none'`) are set in `next.config.ts`; `lib/security-headers.test.ts`
  pins them. `script-src` still needs a per-request nonce because Next emits
  inline bootstrap scripts.
- `react/no-danger` is an ESLint error: third-party text must never be injected
  as HTML. It does not cover `href`/`src`, so later phases validate links.
- ESLint is pinned to `^9.39` because `eslint-plugin-react` (via
  `eslint-config-next`) does not declare ESLint 10 support yet. Pinning keeps
  `npm ci` deterministic.
- Plain `<a>` is used for internal navigation inside the client forms so they
  render in tests without the App Router; the pages are static and the Next
  `Link` component is used everywhere else.

## Status

Landing page, register, sign in, consent, onboarding/profile, document upload
and fact review. Next: discovery and the email studio. The Gmail connection
arrives in Phase 9; until then no email can be sent from this site.
