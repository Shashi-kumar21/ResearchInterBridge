# ResearchInterBridge --- Updated Master Task Specification

**Status:** Consolidated product and engineering requirements\
**Repository:** https://github.com/Shashi-kumar21/ResearchInterBridge\
**Implementation rule:** Audit and extend the existing repository. Do
not create an unrelated replacement or overwrite working components.

------------------------------------------------------------------------

## 1. Goal

Build ResearchInterBridge as a free, open-source and publicly hosted
AI-powered research internship discovery and professor outreach
platform.

Students must be able to access the hosted website through a public URL
without cloning GitHub, register, create an academic profile, upload a
CV and supporting documents, discover relevant international research
opportunities and professors, optimize their CV for selected research
groups, prepare personalized emails, approve outreach, send through
their own Gmail account, and track replies and follow-ups.

The project must support two deployment modes using the same core
codebase:

1.  **Hosted website:** maintained by the project owner and accessible
    through a public URL.
2.  **Self-hosted open-source version:** developers/students can clone
    the public repository, configure their own credentials and deploy
    their own instance.

The platform must remain free for students: no paid subscriptions,
premium tiers, paywalls or mandatory user payments. Apply reasonable
fair-use limits and document external infrastructure/API/AI usage
limitations.

## 2. Final Product Decisions

-   Existing ResearchInterBridge repository is the single source
    codebase.
-   Frontend: Next.js + TypeScript, mandatory.
-   Backend: existing Python application and framework; preserve
    reusable modules. FastAPI may be used for new APIs if the audit
    justifies it.
-   Python 3.10.x compatibility must be preserved unless a migration is
    explicitly approved after dependency and CI review.
-   Email provider for initial release: **Gmail API only**. Support
    personal and supported institutional Google Workspace Gmail accounts
    through official OAuth. Outlook/Microsoft Graph are deferred.
-   Hosted frontend: Vercel or equivalent.
-   Hosted Python backend and persistent workers: Render, Railway or
    equivalent.
-   Hosted database: PostgreSQL. Supabase Auth/Storage may be used after
    auditing compatibility with the existing authentication and storage
    design.
-   SQLite may remain supported for local development/self-hosting where
    appropriate.
-   Use persistent background workers and a durable scheduler for
    long-running discovery, scheduled outreach and Gmail reply
    monitoring.
-   GitHub is the source-code repository and CI/CD system, not the
    hosted product URL.
-   Do not add dependencies or replace infrastructure without first
    auditing the existing implementation and documenting the reason.

## 3. Scope and End-to-End Student Workflow

### 3.1 Account creation, consent and privacy

-   Student registration, login, logout, password/account recovery as
    appropriate, and secure authenticated sessions.
-   Explain personal-data, document-processing and Gmail permissions
    before consent.
-   Isolate each student's profile, documents, email accounts, drafts,
    approvals and outreach records.
-   Provide data export and deletion controls.
-   The hosted website must be usable without GitHub or local setup.

### 3.2 Student onboarding and profile

Collect and maintain: - Name and contact details. - Institution,
department, country and degree. - UG/PG/PhD year, expected graduation
date, CGPA/GPA and grading scale. - Coursework, academic achievements
and eligibility information. - Research interests, subtopics, keywords
and preferred methods. - Technical, programming and laboratory skills. -
Internships, research projects, publications and portfolio links. -
Preferred countries, universities, labs and research areas. - Internship
start/end dates, duration and flexibility. - Funding/stipend
requirements and other constraints.

Only facts confirmed by the student may be used in outbound
communication.

### 3.3 CV and supporting document ingestion

-   Accept PDF, DOCX and TXT initially; OCR fallback for scanned PDFs
    where practical.
-   Validate file extension, MIME type, size and content; reject unsafe
    files.
-   Extract structured facts and show the extracted information to the
    student.
-   Ask the student to resolve uncertain or conflicting facts.
-   Preserve the original uploaded files and track document versions.
-   Never commit private documents or extracted personal data to Git.
-   Never fabricate grades, skills, awards, publications, projects,
    experience or eligibility.

### 3.4 Research internship/program discovery

Prioritize official university, department, laboratory, government and
fellowship sources. For every result, capture: - Program name,
institution, department and country. - Official page and evidence
URLs. - Research areas and opportunity type. - Opening/closing dates,
internship dates and duration. - Eligibility requirements, degree/year
restrictions and application method. - Published stipend/funding,
tuition and accommodation information. - Required documents. - Status:
open, upcoming, closed or unverified. - Last-checked timestamp and
confidence/provenance.

Never invent deadlines, funding or eligibility. Distinguish unknown from
confirmed negative information.

### 3.5 Professor and laboratory discovery

Generate a browsable, filterable list of relevant professors based on
the student's confirmed CV/profile, research interests, skills,
projects, academic background, timeline and preferences.

For every professor show: - Verified full name, institution, department,
country and laboratory. - Official faculty/lab profile URL. - Public
institutional email, if officially listed and verified. - Research
interests, methods, themes and relevant projects. - Two or three
relevant recent publications, prioritizing the last three years where
possible. - Publication title, authors, year, DOI/official URL and
abstract when available. - Short explanation of relevance to the
student's profile. - Active recruitment/internship evidence, if
available. - Match score, eligibility notes, confidence and last
verification date.

Use official faculty directories/lab pages and reputable scholarly
sources such as OpenAlex, Crossref, Semantic Scholar, arXiv where
relevant, and institutional repositories.

Verification rules: - Do not guess email addresses from naming
patterns. - Do not merge professors with similar names. - Do not
fabricate publications, authors, abstracts or dates. - Mark
unavailable/unverified contact data clearly and exclude it from
sending. - Every material claim must have a source link. - Let students
search, filter, compare, shortlist, select and exclude professors.

### 3.6 Matching and ranking

Provide a transparent configurable 0--100 match score based on: -
Research-interest similarity. - Skills, coursework and project
relevance. - Publication-topic alignment. - Degree/year eligibility. -
Internship timeline compatibility. - Funding requirements and confirmed
funding availability. - Country/university/department preferences. -
Evidence of active recruitment.

Separate hard eligibility constraints from soft ranking. A high
similarity score must never hide a failed mandatory eligibility
condition. Explain each score and identify unknowns/gaps.

### 3.7 Professor-specific CV analysis and optimization

Use the same original CV uploaded by the student as the source document
for all applications.

For each selected professor/lab: - Compare the student's confirmed
CV/profile with the professor's research interests, lab work and
relevant publications. - Suggest targeted improvements to project
descriptions, research experience, technical skills, coursework,
keywords, ordering and clarity. - Highlight every proposed modification
and explain why it is relevant to that research group. - Display
original and suggested content side by side or as a clear diff. - Allow
the student to refine suggestions. - Never silently modify or overwrite
the original CV. - Never fabricate or exaggerate skills, achievements,
publications, grades, experience or results. - Require explicit student
approval before generating a revised CV or attaching it. - Maintain the
original CV and separately versioned, professor-specific approved CV
versions. - Let the student choose the approved CV version attached to
each email. - Email generation must not assume that unapproved CV
suggestions have been accepted.

### 3.8 Personalized email generation

Generate one distinct draft for every selected professor, with the
ability to generate drafts for a selected batch.

Professor-specific personalization must update only verified
professor-related details: - Professor's verified name. - Verified
recipient email. - Relevant research topic. - Latest relevant
publication, with correct title and source link. - Specific research
alignment between the professor's work and the student's confirmed
experience.

Student-specific information must come from the student's confirmed
profile and uploaded CV. Preserve the student's academic background,
achievements, research experience, career goals and factual claims
consistently. Do not rewrite or invent student credentials to fit a
professor.

Draft requirements: - Professional subject and concise email body. -
Student introduction and internship period based on confirmed
profile/preferences. - Accurate reference to the professor's
research/publication. - Evidence-backed explanation of research fit. -
Polite internship request and closing. - Student-selected approved CV
version and other explicitly selected attachments. - Show
sources/evidence used for professor-specific claims. - Allow edit,
preview, save, version history and regenerate. - Flag missing or
unverified professor details rather than guessing. - A fixed template
may guide structure, but the research paragraph must be genuinely
personalized.

### 3.9 Batch review and single approval action

-   Student selects multiple professors from the shortlist.
-   Generate separate personalized drafts for every selected professor.
-   Show one consolidated review screen with professor name, verified
    recipient, university, subject, full body, sender Gmail account,
    selected CV attachment, evidence and planned local send time.
-   Allow each draft to be edited or excluded before approval.
-   Provide one batch approval action for the explicitly selected,
    individually reviewable drafts.
-   The batch action must create a separate approval record for every
    included draft.
-   Bind each approval to the exact draft version, recipient, sender
    account and selected attachment/version.
-   A batch approval is not blanket permission for unselected, future or
    newly generated drafts.
-   Any change to body, subject, recipient, sender or attachment after
    approval invalidates that draft's approval and requires fresh
    approval.
-   Only individually approved drafts may be scheduled/sent.
-   Show per-professor send status and errors.

### 3.10 Gmail OAuth and actual sending

Gmail is the only supported provider in the initial release.

-   Support personal Gmail and institutional Google Workspace Gmail
    where organization policy permits.
-   Support multiple connected Gmail accounts and explicit sender
    selection.
-   Use official Google OAuth authorization-code flow; use PKCE where
    supported by the chosen client/library.
-   Configure consent screen, production/local redirect URIs and
    least-privilege scopes.
-   Create drafts, send approved messages, read only authorized relevant
    thread metadata and track Gmail message/thread IDs.
-   Support token encryption at rest, refresh, expiry, revocation,
    disconnect and reconnect.
-   Never request Gmail passwords or app passwords.
-   Never put OAuth tokens in source code, prompts, Git or logs.
-   Workspace administrator restrictions must be explained; discovery
    and drafting should remain usable if Gmail connection is
    unavailable.
-   Report `sent` only after Gmail confirms the send and returns the
    provider result.
-   Do not implement Outlook/Microsoft Graph in the initial release;
    keep provider boundaries modular for possible future support.

### 3.11 Scheduling and outreach policies

Enforce policies in deterministic backend code, not only in prompts.

Recipient-local send window: - Monday--Friday. - 09:00 inclusive to
17:00 exclusive. - Use verified recipient IANA timezone. - Handle
daylight saving correctly. - If timezone is not verified, do not
schedule automatically. - Display recipient timezone and planned local
send time.

Blackouts: - No sending during December. - Support configurable
institution/country-specific summer closures, public holidays and
institutional holidays. - Do not assume all countries/universities have
the same summer break. - If a relevant closure is unknown, request
configuration rather than inventing dates.

University cap: - Maximum five initial outreach emails per university
per local calendar day. - Count across all connected accounts, aliases,
workers and concurrent jobs. - Enforce atomically in the
database/transactional service. - Account switching or aliases must not
bypass the cap. - Idempotent retry of the same outreach must not create
a duplicate or consume another slot. - Queue excess approved messages
for the next eligible window. - Follow-ups also obey sending windows,
blackout and rate-limit policies.

Queue: - Persist jobs independently of the browser. - Revalidate
eligibility immediately before sending. - Support pause, resume, cancel,
retry and restart recovery. - Use idempotency keys and provider IDs to
prevent duplicate sends. - If a message becomes ineligible, pause it and
notify the student.

### 3.12 Reply classification and positive-response workflow

Monitor only Gmail mailboxes/threads the student authorized. Match
replies using provider thread/message IDs and normalized identities, not
subject line alone.

Classify incoming responses into: 1. Positive interest in
hosting/discussing an internship. 2. Meeting/interview invitation. 3.
Request for CV, transcript or additional information. 4. Neutral
acknowledgement. 5. Rejection/position unavailable. 6. Out-of-office
response. 7. Bounce/delivery failure. 8. Stop/unsubscribe request.

For every classification: - Store classification, confidence, supporting
thread evidence and timestamp. - Mark uncertain cases for manual
review. - Avoid treating out-of-office messages as substantive replies
where reliably detectable.

For positive replies: - Create a dedicated Positive Replies dashboard
view. - Notify the student. - Summarize the professor's response and
requested next steps. - Generate a context-aware response draft for
student review and fresh approval. - For meeting requests, highlight
proposed dates/times and allow a confirmation or alternative-time
draft. - For document requests, identify requested files and require the
student to select/approve attachments. - Never automatically send
replies.

### 3.13 Three-working-day follow-up

-   After three recipient-local working days without a relevant reply,
    notify the student and prepare a personalized follow-up draft
    referencing the original thread.
-   Calculate working days using recipient-local calendar and configured
    holidays.
-   Require fresh explicit approval before sending.
-   Suppress follow-ups after meaningful reply, bounce, rejection or
    stop request.
-   Default to a maximum of one follow-up per professor unless
    explicitly changed by the student and allowed by policy.
-   Apply the same sending window, blackout, cap/rate and audit rules.

### 3.14 Dashboard and notifications

Provide: - Profile completeness and confirmed facts. - Uploaded
documents and CV versions. - CV suggestions, highlighted edits and
approval status. - Programs and deadlines. - Professor list, profiles,
papers, match scores and shortlist. - Draft editor and consolidated
batch review/approval. - Connected Gmail accounts and sender
selection. - Scheduled, sent, failed and bounced outreach. - Dedicated
Positive Replies and meeting-request views. - Neutral replies,
rejections and closed outreach. - Follow-ups due and pending approval. -
Notifications, audit trail and actionable errors. - Export/delete
controls and account settings.

## 4. MCP Integration

Extend the existing MCP implementation. MCP is a typed tool interface,
not a replacement for application authentication, authorization, policy
enforcement or audit logging.

Provide narrowly scoped tools for: - Program and professor discovery. -
Verified faculty profile and publication lookup. - Authenticated student
profile and confirmed document facts. - CV analysis and proposed
changes. - Draft creation and retrieval. - Batch review/approval
records. - Connected Gmail accounts. - Approved sending. - Thread/reply
classification and status. - Scheduling and outreach status.

Use typed schemas, authenticated user context, record ownership checks
and stable IDs. Do not expose unrestricted
`send_email(to, subject, body)`. `send_approved_email` must accept
outreach/draft ID and approval ID, not arbitrary LLM-generated body
text. MCP must call the same backend policy services as the UI. Use
local stdio for local development; secure authenticated remote MCP only
if hosted deployment requires it.

## 5. Technical Architecture and Deployment

### Frontend

-   Next.js + TypeScript responsive public-facing website.
-   Landing page, registration/login, onboarding, dashboard, profile,
    document upload, discovery, professor list/profile, CV optimizer,
    email studio, batch approval, outreach tracker, positive replies,
    follow-ups and settings.
-   Authenticated API integration with the Python backend.
-   Frontend foundation begins early; do not postpone all frontend work
    until the final phase.

### Backend

-   Extend existing Python backend/framework and reusable modules.
-   FastAPI may be introduced for APIs if justified by audit.
-   Keep authentication, matching, CV versioning, approval, scheduling,
    daily caps, sending and audit policies in backend services.
-   Preserve Python 3.10.x compatibility unless explicitly approved.

### Data and background services

-   PostgreSQL for hosted multi-user production.
-   SQLite may remain for local development/self-hosting.
-   Supabase Auth/Storage are optional implementation choices after
    architecture audit, not mandatory dependencies.
-   Redis + Celery or equivalent only if justified for persistent
    background jobs.
-   Workers and scheduler must run independently from
    frontend/serverless request lifetimes.

### Hosting

-   GitHub: source repository, version control and CI.
-   Vercel: Next.js frontend hosting.
-   Render/Railway/equivalent: Python API and persistent workers.
-   Supabase/equivalent: PostgreSQL, authentication and storage if
    selected.
-   Configure secrets, environment variables, CORS, API endpoints and
    Google OAuth production redirects securely.
-   Provide provider-generated URL initially; custom domain can be added
    later.
-   Pushing/merging approved changes to the configured production branch
    should trigger the documented deployment workflow.
-   Document free-tier limits, external API/AI usage limits and any
    infrastructure costs. Do not promise unlimited zero-cost hosting.

### Deployment modes

1.  Hosted: students use the public website without cloning GitHub or
    configuring their own backend.
2.  Self-hosted: clone the same public repository, configure credentials
    and run using documented PowerShell and Docker Compose instructions.

Both modes must use the same core application code.


## 6. LLM Provider and API Configuration

- Implement a configurable LLM provider architecture. Do not hardcode a single provider or model.
- Select the initial provider and model after evaluating quality, structured output reliability, latency and API cost.
- Support configurable models for:
  - CV parsing and profile extraction.
  - Research paper summarization.
  - Professor-student research matching.
  - CV optimization suggestions.
  - Personalized email generation.
  - Reply classification and response drafting.
- Store API keys securely as backend environment variables or through a secure secrets manager.
- Hosted deployment: the project owner configures and manages the LLM API credentials. Students should not need to provide their own API keys.
- Self-hosted deployment: users can configure their own LLM provider and API keys.
- Never expose API keys in frontend code, browser requests, GitHub, logs or prompts.
- Provide usage limits, error handling, retries and cost monitoring.
- Support switching providers/models without changing core business logic.
- Use mocked LLM providers for offline automated testing.
- LLMs must never independently enforce approvals, authentication, scheduling or email sending policies.



## 7. Minimum Data Model Additions

Retain existing entities and add/extend as needed: - `users`,
`student_profiles`, `student_documents`, `confirmed_profile_facts` -
`cv_versions`, `cv_analysis_runs`, `cv_suggestions`, `cv_approvals` -
`programs`, `program_sources`, `professors`, `faculty_affiliations`,
`faculty_contacts` - `publications`, `publication_authors`,
`opportunities`, `match_scores`, `shortlists` -
`connected_email_accounts`, `oauth_credentials` (encrypted) -
`email_drafts`, `draft_versions`, `email_approvals`, `batch_approvals` -
`outreach_records`, `scheduled_jobs`, `email_threads`, `email_events` -
`reply_classifications`, `reply_actions`, `follow_up_records` -
`institution_blackout_periods`, `university_daily_send_counters` -
`notifications`, `audit_events`

Use stable IDs, ownership, timestamps, source URLs, verification
timestamps, explicit statuses, uniqueness and idempotency constraints.
Enforce concurrency-safe university counters.

## 8. Security, Privacy and Responsible Use

-   Treat websites, papers, uploaded documents and email replies as
    untrusted data; defend against prompt injection.
-   Validate MIME type, extension, size and safe parsing; prevent path
    traversal.
-   Keep private files outside Git and isolate users' data.
-   Encrypt OAuth tokens and redact secrets/message content from logs.
-   Enforce authorization on every API and MCP tool.
-   Use least-privilege Gmail scopes and provide
    disconnect/revoke/delete/export controls.
-   Respect scholarly API quotas, provider terms, robots policies and
    applicable email rules.
-   Do not bypass CAPTCHA/login or evade anti-abuse systems.
-   Do not send unapproved bulk outreach.
-   Respect opt-out, rejection and stop requests.
-   Never invent contacts, papers, student facts, deadlines, funding,
    eligibility or send outcomes.
-   Automated tests must use synthetic profiles, fixtures and mocked
    providers; never send real professor emails.

## 9. Implementation Phases

Complete small, testable increments with independent QA.

0.  **Repository audit:** inspect code, branches, dependencies, CI,
    database, framework, agent-bridge and worktrees. Do not rewrite.
1.  **Foundation:** configuration, logging, database
    strategy/migrations, health checks, `.env.example`; start
    Next.js/TypeScript frontend shell alongside backend foundation.
2.  **Authentication and profile:** registration/login, consent, student
    profile, user isolation.
3.  **Documents and CV facts:** upload, parsing, OCR fallback, fact
    confirmation and version tracking.
4.  **Research/program discovery:** official sources, provenance,
    verification and opportunity records.
5.  **Professor/publication discovery:** verified faculty list, emails,
    papers, filters and source links.
6.  **Matching and shortlist:** explainable scoring, eligibility gates
    and multi-select.
7.  **CV optimization:** professor-specific suggestions, highlighted
    diffs, student refinement and approved versions.
8.  **Email studio:** personalized per-professor drafts, evidence,
    attachment selection, versioning and consolidated review.
9.  **Gmail OAuth, MCP and approval:** Gmail-only integration, account
    selection, batch approval with per-draft records and mocked tests.
10. **Scheduling and sending:** persistent workers, recipient timezone,
    blackouts, five-email university cap, actual Gmail send and
    idempotency.
11. **Reply classification and follow-ups:** positive reply workflow,
    meeting/document requests, three-working-day follow-up drafts and
    approval.
12. **Dashboard completion and notifications:** integrate all workflows
    and audit history.
13. **Security, deployment and release:** production hardening,
    Vercel/backend/DB/workers, hosted URL, Docker Compose self-hosting,
    backup/recovery and QA sign-off.

Frontend foundations and screens should be developed incrementally
alongside the corresponding backend APIs; do not defer the entire
website to the dashboard phase.

## 10. Acceptance Criteria and Tests

At minimum test: 1. Registration, authentication, session handling and
user-level data isolation. 2. PDF/DOCX/TXT parsing,
scanned/malformed/oversized/unsafe documents. 3. Confirmation of
extracted facts and conflict resolution. 4. Original CV preservation and
CV version history. 5. Professor-specific CV suggestions, highlighted
diffs and approval before revised attachment. 6. Verified/unverified
faculty identity and email handling. 7. Publication relevance, author
resolution and source attribution. 8. Program status, deadlines, funding
and unknown eligibility. 9. Explainable scores and hard eligibility
failures. 10. Professor search, filters, shortlist, multi-select and
deduplication. 11. Professor-specific draft personalization changes
professor details while preserving confirmed student facts. 12. Missing
professor evidence is flagged rather than fabricated. 13. Batch approval
creates separate approvals for exact selected draft versions. 14. Draft,
recipient, sender or attachment edits invalidate approval. 15. Gmail
personal/Workspace OAuth, account selection, token refresh, expiry,
revocation and missing scopes. 16. Only approved drafts can be sent;
provider confirmation required for `sent`. 17. Gmail thread lookup and
reply classification for positive, meeting, document request, neutral,
rejection, OOO, bounce and stop. 18. Positive-reply response drafts and
requested-attachment selection require fresh approval. 19. Follow-up
after three recipient-local working days;
replies/bounces/rejections/stops suppress it. 20. Monday--Friday, 09:00
inclusive--17:00 exclusive, IANA timezone and DST boundaries. 21.
December and configured summer/holiday blackouts. 22. Atomic
five-initial-emails-per-university-per-local-day cap across accounts and
concurrent workers. 23. Idempotent retries and restart recovery. 24. MCP
authentication, schema validation and rejection of policy bypass
attempts. 25. Secrets, file privacy, audit events and authorization. 26.
Hosted frontend/backend/API/DB/worker integration and public URL access
without cloning. 27. Deployment automation from configured branch. 28.
Docker Compose and documented self-host setup. 29. Synthetic end-to-end
workflow with mocked Gmail/research providers.

Preserve existing CI checks and verify actual workflow matrix/check
names. Keep CI offline and deterministic. Run actual project checks,
including:

``` powershell
python --version
python -m ruff check .
python -m pytest -q
```

Run frontend lint/typecheck/tests/build using the actual configured
package scripts. Do not claim tests or deployment passed without command
output/evidence.

## 11. Dev--QA Collaboration and Repository Rules

-   Use existing `agent-bridge` only after verifying daemon and agent
    status with actual installed CLI help/list commands.
-   Dev implements bounded feature slices and tests; QA independently
    verifies code and evidence.
-   Avoid simultaneous edits to the same files; use separate
    branches/worktrees or sequential ownership.
-   Preserve protected `main`, current GitHub Actions, migrations and
    working modules.
-   Inspect `git status`, branches, diffs and CI before changes.
-   Never force-push, reset user data, delete existing work or replace
    working infrastructure without explicit approval.
-   Report exact files changed, commands, actual outputs, failures,
    blockers and remaining risks.

## 12. Required Deliverables

1.  Public hosted website and deployment URL.
2.  Open-source repository supporting self-hosting.
3.  Next.js + TypeScript frontend and authenticated Python backend.
4.  Student registration, profile and secure document management.
5.  Program and professor discovery with verified sources.
6.  Explainable matching and shortlist.
7.  Professor-specific CV analysis, highlighted suggestions and approved
    CV versions.
8.  Personalized professor email drafts and consolidated batch review.
9.  Gmail-only OAuth, actual approval-gated sending and thread tracking.
10. Persistent scheduling, recipient-local policy and university daily
    cap.
11. Reply classification, positive-response workflow and approved
    follow-ups.
12. MCP tools with typed schemas and backend authorization.
13. PostgreSQL production setup and persistent background workers.
14. Docker Compose and PowerShell self-hosting documentation.
15. `.env.example` placeholders only, migration/backup/recovery docs.
16. Unit, integration, security and end-to-end tests.
17. Independent QA report with evidence, known limitations and
    production risks.

## 13. Non-Negotiable Rules

-   Free student access; no paid tiers or paywalls.
-   Hosted public website and self-hosted open-source deployment use the
    same core code.
-   Extend the existing repository; do not perform an uncontrolled
    rewrite.
-   Gmail only for initial release; Outlook is deferred.
-   Student facts come from the uploaded, confirmed CV/profile and must
    remain consistent across professor drafts.
-   Professor name, email, topic and publication must be verified; never
    guess.
-   CV suggestions are highlighted and require student
    refinement/approval; original CV is never silently changed.
-   Batch approval applies only to selected, reviewed, unchanged drafts
    and creates individual approval records.
-   Every reply, follow-up and revised draft requires the appropriate
    fresh student approval.
-   Enforce the five initial emails per university per local day,
    weekday 09:00--17:00 recipient-local window, December blackout,
    configured closures and three-working-day follow-up.
-   Backend code---not the LLM---enforces authentication, authorization,
    approvals, scheduling, caps and idempotency.
-   No real professor emails in automated tests.
-   Never claim production readiness without passing tests and
    independent QA evidence.
