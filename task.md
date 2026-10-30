# ResearchInterBridge — Complete Product Workflow & Engineering Specification

> **Purpose:** Build a production-oriented, local-first AI agent that helps undergraduate and postgraduate students discover suitable international research internships, identify relevant professors and research papers, prepare personalized outreach, and manage approved email communication and follow-ups.
>
> **Source of truth:** This document is the product and implementation specification. Dev and QA agents must read it before making changes. Do not assume a feature is complete until its acceptance criteria pass.

---

## 0. Project Context and Existing Setup

- Project: `ResearchInterBridge`
- Primary development OS: Windows 10/11
- Shell: Windows PowerShell
- Python compatibility: Python 3.10.x
- Backend preference: Python; retain the existing framework and code where sound.
- Existing coordination project: [agent-bridge](https://github.com/Shashi-kumar21/agent-bridge)
- Main working directory: `C:\Users\shash\new_project\ResearchInterBridge`
- QA worktree: `..\ResearchInterBridge-qa`
- Git setup reported by the owner:
  - Public GitHub repository created.
  - `main` is protected.
  - Four required checks: `lint` and `test` on three operating systems.
  - Development branch and QA worktree prepared.
  - Agent-bridge daemon version 0.1.2 was being used; a start command timed out. Verify actual status using the installed `listagents` command before assuming agents are running.

### First action: audit, do not rewrite

Before implementation, inspect:

1. Repository tree, README, `docs/`, task instructions and existing source.
2. Git branch, status, remotes, protected-branch workflow files and CI matrix.
3. Python version, virtual environment, installed dependencies and test commands.
4. Existing FastAPI/app structure, database, migrations and configuration.
5. `agent-bridge` README, CLI help, daemon status and actual agent list.
6. Existing Dev and QA worktree state.

Do not overwrite files, reset databases, force-push, delete user data or replace working infrastructure without explicit approval.

---

# 1. Product Goal

Create a student-facing AI research internship assistant that can:

1. Collect a student's academic profile, interests, target countries/universities and internship timeline.
2. Read CVs and supporting documents, extract facts and ask the student to confirm uncertain information.
3. Find suitable research internship programs, visiting student opportunities, fellowships and funded research positions.
4. Discover professors and laboratories relevant to the student's research interests.
5. Return verified professor names, institutional email addresses, research interests and relevant recent research papers.
6. Explain why each opportunity is a match and identify eligibility gaps.
7. Generate genuinely personalized email drafts grounded in the student's profile and the professor's research.
8. Connect personal and institutional Gmail accounts, and optionally Outlook, using official OAuth flows.
9. Send emails through connected accounts only after the required user approval.
10. Schedule messages in the professor's local working hours and enforce sending limits.
11. Detect replies, notify the student and prepare a follow-up draft after three working days without a reply.
12. Maintain persistent records, audit logs, error handling and restart-safe workflows.

The product must do real work through verified sources and connected services. It must not present fabricated contacts, papers, opportunities or send results.

---

# 2. End-to-End User Journey

## Step 1 — Account creation and consent

- Student creates an account or local profile.
- Explain what personal data and documents will be stored.
- Ask for consent before processing uploaded documents or connecting external accounts.
- Provide controls to export or delete the student's profile and documents.
- Store each user's data separately; never expose one student's profile to another.

**Output:** authenticated student workspace and consent record.

## Step 2 — Student onboarding

Collect the following information:

### Identity and academics
- Full name and preferred name.
- Contact email; optional phone.
- Current institution, department and country.
- Degree type: BTech, BE, BS, MSc, MTech, integrated dual degree, PhD or other.
- UG/PG/PhD year and expected graduation date.
- CGPA/GPA, grading scale and relevant academic achievements.
- Relevant coursework.

### Research profile
- Main research interests and subtopics.
- Keywords and preferred research methods.
- Technical skills, programming languages and laboratory skills.
- Previous internships, research projects and publications.
- Portfolio, GitHub or personal website.

### Internship preferences
- Earliest start date and latest end date.
- Preferred duration and date flexibility.
- Target countries, universities, departments, laboratories or professors.
- Opportunity types: funded internship, visiting student, summer/winter research, fellowship, research assistantship or other.
- Funding/stipend requirement, accommodation needs and travel constraints.
- Any eligibility constraints the student chooses to provide.

### Supporting documents
- CV/resume.
- Academic transcript.
- Statement of purpose.
- Recommendation letters, if available.
- Portfolio or project reports.
- Other relevant documents.

**Rules:**
- Accept PDF, DOCX and TXT initially; add OCR for scanned documents where practical.
- Validate file type and size; reject unsafe or unsupported files.
- Extract facts into structured fields and show the student what was extracted.
- Ask the student to confirm conflicting or uncertain values.
- Never invent grades, skills, publications, awards, eligibility or work authorization.
- Keep original files and track versions.
- Do not commit student documents or private profile data to Git.

**Output:** confirmed, structured student profile and document inventory.

## Step 3 — Profile analysis

The agent creates a research profile containing:

- Normalized research-interest taxonomy and keywords.
- Skills and methods.
- Relevant courses and projects.
- Degree/year and graduation context.
- Internship availability window.
- Funding and location preferences.
- Evidence references pointing back to the student's confirmed profile or documents.

The student can edit the generated profile. Only confirmed facts may be used in outbound emails.

**Output:** searchable profile representation for matching.

## Step 4 — Program discovery

Search reliable public sources, prioritizing:

1. Official university internship and summer research pages.
2. Official department and laboratory pages.
3. Government and institutional scholarship portals.
4. Official fellowship and exchange-program pages.
5. Other reputable sources only as supplementary evidence.

For every program, capture:

- Program name.
- University/institution, department and country.
- Official program URL and evidence URLs.
- Program type and research areas.
- Application opening and closing dates.
- Internship start/end dates and duration.
- Eligibility requirements, including degree/year restrictions.
- Stipend, scholarship, tuition and accommodation information, if published.
- Required documents and application method.
- Whether the program is open, upcoming, closed or unverified.
- Last checked timestamp and confidence/evidence status.

**Rules:**
- Do not label a program open unless an authoritative source supports that status.
- Distinguish unknown information from negative information.
- Do not infer funding or eligibility from unrelated programs.
- Respect website terms, robots policies, rate limits and API conditions.
- Prefer official APIs and structured sources over aggressive scraping.

**Output:** sourced opportunity records.

## Step 5 — Professor and laboratory discovery

Search official faculty directories, university department pages, laboratory websites and scholarly indexes.

Suggested scholarly sources:
- OpenAlex.
- Crossref.
- Semantic Scholar.
- arXiv, where relevant.
- Official university publication repositories.

For each professor, return:

- Full name.
- Verified university and department.
- Country and laboratory.
- Official faculty/lab webpage.
- Publicly listed institutional email, if available.
- Research interests.
- Relevant methods, projects and themes.
- Two or three relevant recent publications, prioritizing the last three years when possible.
- Paper title, authors, year, DOI or official paper URL and abstract when available.
- Short explanation of relevance to the student's interests.
- Evidence of active research or advertised internship opportunities, if found.
- Identity/affiliation confidence and last-verified date.

**Verification rules:**
- Prefer official university pages for identity, affiliation and email.
- Resolve authors carefully; do not merge people with similar names.
- Never guess an email from a naming convention.
- Mark missing/unverified email clearly and exclude it from sending.
- Do not fabricate paper titles, authors, abstracts or publication dates.
- Link every material claim to its source.

**Output:** deduplicated professor and paper records with provenance.

## Step 6 — Matching and ranking

Calculate a transparent 0–100 match score. Suggested configurable factors:

- Research-interest similarity.
- Relevant skills, coursework and project experience.
- Publication-topic alignment.
- Degree/year eligibility.
- Internship timeline compatibility.
- Funding requirements and published funding availability.
- Country, university and department preferences.
- Evidence of active recruitment or internship availability.

Separate hard eligibility constraints from soft ranking preferences.

Example output for each opportunity:

- Overall match score.
- Factor-by-factor score breakdown.
- Why it matches.
- Relevant student skills/projects.
- Relevant professor papers.
- Eligibility passed/failed/unknown.
- Timeline fit.
- Funding status: confirmed, not stated or unknown.
- Missing information and recommended next action.

A high research score must never override a failed mandatory eligibility requirement.

Provide filters for country, university, timeline, research area, funding and opportunity type. Deduplicate programs, faculty, papers and prior outreach.

**Output:** ranked shortlist with explainable evidence.

## Step 7 — Student review and shortlist

The dashboard lets the student:

- Save or reject a program/professor.
- Mark an opportunity as interesting.
- Compare professors and programs.
- Open source pages and research papers.
- Correct profile or matching information.
- Select which professors to contact.

No email should be sent merely because a professor appears in search results.

**Output:** student-approved outreach shortlist.

## Step 8 — Personalized email drafting

Create a separate draft for each professor.

Each draft should include:

- Appropriate subject line.
- Concise student introduction.
- Exact proposed internship dates and duration.
- A specific reference to a verified recent paper or research topic.
- Why the student's experience aligns with the professor's work.
- A clear and polite request to discuss a research internship opportunity.
- Relevant skills/projects only.
- Funding context, if useful and truthful.
- Professional closing and signature.
- Only the attachments selected by the student.

### Draft quality rules

- Do not use generic mass-email wording.
- Do not claim the professor's paper says something unless supported by the paper/source.
- Do not fabricate familiarity, prior conversations or student achievements.
- Keep emails concise and professional.
- Show the evidence used to personalize each email.
- Save draft versions and preserve edit history.
- If a draft is modified after approval, invalidate the old approval and require fresh approval.

**Output:** editable, evidence-backed draft.

## Step 9 — Connect Gmail and personal/institutional accounts

The product must support connecting:
- Personal Gmail accounts.
- Institutional Google Workspace Gmail accounts, subject to organization policy.
- Microsoft Outlook accounts as an optional provider.

### Gmail setup

1. Create a Google Cloud project.
2. Enable the Gmail API.
3. Configure the OAuth consent screen.
4. Create an OAuth client appropriate to the deployment.
5. Configure allowed redirect URIs for local development and production.
6. Implement the official OAuth authorization-code flow; use PKCE where supported by the selected client/library.
7. Ask the student to sign in and grant only the scopes required for the chosen features.
8. Display the connected email identity and granted permissions.
9. Let the student choose which connected account sends each message.
10. Provide disconnect/revoke controls.

Suggested least-privilege scope strategy:
- Start with identity/profile access needed to identify the connected account.
- Request `gmail.compose` when draft creation and sending are enabled.
- Request `gmail.readonly` only when inbox/thread monitoring is enabled.
- Do not request broader mailbox permissions without a documented product need.

Institutional Workspace administrators may restrict third-party OAuth apps. Explain permission failures and keep research discovery/drafting usable if Gmail access is unavailable.

### Token security

- Never ask for Gmail passwords or app passwords.
- Never place OAuth tokens in prompts, source code, Git, task files or logs.
- Store tokens encrypted at rest using an appropriate secret-management design.
- Support token refresh, expiry, revoked consent and disconnect.
- Redact tokens and sensitive message content from logs.
- Use separate provider adapters so Gmail and Outlook can be tested independently.

### Gmail capabilities

Through the official Gmail API, support:
- List connected sender accounts.
- Create drafts.
- Send an approved draft/message.
- Read relevant message/thread metadata for reply detection.
- Track provider message ID and thread ID.
- Report actual send success/failure from the provider response.

Do not report a message as sent merely because it was drafted, approved or queued.

**Output:** connected sender account with scoped authorization and tested provider adapter.

## Step 10 — MCP server integration

Use the Model Context Protocol (MCP) as a typed tool interface between the AI orchestrator and application services. MCP does not replace OAuth, user authentication, authorization, business rules or audit logging.

### Recommended MCP servers

**A. Research MCP**
- Search programs.
- Search faculty.
- Retrieve verified faculty profile.
- Search relevant papers.
- Retrieve paper details and source evidence.

**B. Student/Profile MCP**
- Retrieve the authenticated student's confirmed profile.
- Retrieve approved document extracts.
- Read selected CV facts.
- Do not expose raw private documents to unrelated tools by default.

**C. Email MCP**
- List connected email accounts.
- Create email draft.
- Retrieve thread status.
- Detect relevant replies.
- Send an approved outreach message.
- Schedule an approved outreach item.

**D. Scheduler/Outreach MCP**
- Check recipient timezone.
- Preview eligible send time.
- Check university daily cap.
- Schedule approved messages.
- Retrieve outreach and follow-up status.

### Proposed tool names

These are application tool names to implement, not assumed existing commands:

- `search_programs`
- `search_faculty`
- `get_verified_faculty_profile`
- `search_relevant_papers`
- `get_student_profile`
- `get_confirmed_document_facts`
- `list_connected_email_accounts`
- `create_email_draft`
- `get_email_thread_status`
- `schedule_approved_outreach`
- `send_approved_email`
- `get_outreach_status`

### MCP security contract

- Use typed JSON schemas and validate all inputs/outputs.
- Authenticate the calling user and check record ownership.
- Return structured results with stable IDs and source/provenance fields.
- Do not expose arbitrary database or filesystem access.
- Do not expose an unrestricted `send_email(to, subject, body)` tool to the LLM.
- `send_approved_email` must accept an outreach/draft ID and approval ID, not arbitrary unreviewed body text.
- The backend must re-check approval version, selected sender, recipient, schedule, blackout dates, university cap, reply/stop state and idempotency immediately before sending.
- An MCP call must never bypass the same policy service used by the UI.
- Record tool invocations and consequential actions in the audit log.

### Local MCP development

- Prefer a local stdio MCP server for Windows development where supported by the agent host.
- Use the official MCP SDK for the selected language.
- Keep secrets in environment variables or a secure local store.
- Inspect the installed OpenCode version and its current MCP configuration documentation before writing configuration; do not invent config keys.
- Add a mock MCP server/provider so tests run offline.
- Consider authenticated Streamable HTTP only when a separately hosted deployment needs it; secure transport and authentication before exposing it remotely.

**Output:** documented MCP servers and tools, with tests proving backend policy enforcement.

## Step 11 — Scheduling rules

Scheduling must be implemented in deterministic backend code, not only in an LLM prompt.

### Recipient-local sending window

- Monday through Friday.
- 09:00 to 17:00 inclusive of the start and exclusive of the end.
- Use the recipient's verified IANA timezone.
- Handle daylight-saving changes correctly.
- Use the professor's university location when verified.
- If timezone cannot be verified, do not schedule automatically.
- Show the student the recipient timezone and planned local send time.

### Blackout dates

- No sending during December.
- Support institution/country-specific summer closure periods.
- Support configurable public holidays and institution closures where reliable data is available.
- Do not assume every country's summer break occurs in the same months.
- Make configured blackout periods visible and editable.
- If the relevant summer period is unknown, ask the student/admin to configure it rather than inventing dates.

### University daily cap

- Maximum five **initial outreach emails per university per local calendar day**.
- Count across all connected accounts, aliases, workers and concurrent jobs.
- Enforce atomically in the database or equivalent transactional service.
- Do not allow account switching or aliases to bypass the cap.
- A retry of the same outreach must not create a duplicate message or consume a second initial-outreach slot.
- When the cap is reached, queue remaining approved emails for the next eligible window.
- Follow-ups must also respect sending windows, blackout rules and configured rate limits.

### Queue behavior

- Persist scheduled jobs.
- Recalculate eligibility before sending, not only when queued.
- If a message becomes ineligible, pause it and notify the student.
- Use idempotency keys and provider IDs to prevent duplicate sends after timeouts or restarts.
- Provide pause/resume and cancel controls.

**Output:** deterministic, persistent and restart-safe scheduler.

## Step 12 — Send approval and execution

### Required workflow

1. Agent creates a draft.
2. Student sees sender account, recipient, university, subject, full body, evidence, attachments and planned local send time.
3. Student explicitly approves that exact version.
4. Backend records approval against the draft version and selected sender account.
5. Scheduler waits for a permitted local sending window.
6. Immediately before sending, backend revalidates all policies.
7. Provider sends the message.
8. Backend records actual provider result, message ID, thread ID and timestamp.
9. Dashboard displays the truthful status.

Statuses should distinguish:
- Draft.
- Awaiting approval.
- Approved.
- Scheduled.
- Sending.
- Sent.
- Failed.
- Bounced, if detected.
- Replied.
- Follow-up due.
- Closed/stopped.

Initial outreach and follow-ups require approval. An optional batch approval may be supported only for an explicitly selected, user-reviewed set of unchanged drafts. Any changed draft requires fresh approval.

**Output:** safe, auditable sending workflow.

## Step 13 — Reply monitoring and follow-up

- Monitor only the connected mailbox/threads the student authorized.
- Match replies using provider thread/message IDs and normalized sender/recipient identities; do not rely only on subject line.
- Detect replies, bounces, rejection/stop requests and other relevant thread changes.
- Avoid treating automated out-of-office messages as a substantive professor reply where they can be reliably identified.
- Notify the student when a meaningful reply is detected.
- Stop pending follow-ups when a reply, bounce, rejection or stop request is recorded.

### Three-working-day rule

If no relevant reply is detected after three working days:
1. Calculate the interval using the recipient's local calendar and configured holidays.
2. Notify the student.
3. Prepare a personalized follow-up draft referencing the original outreach.
4. Show the draft and thread context.
5. Require explicit approval before sending.
6. Apply the same local sending window, blackout and rate-limit rules.
7. Record the follow-up outcome.

Do not send repeated reminders indefinitely. Configure a maximum follow-up count and cooldown; default to one follow-up unless the student changes it.

**Output:** reply-aware follow-up draft and notification workflow.

## Step 14 — Dashboard and notifications

Dashboard sections:

- Profile completeness.
- Uploaded documents and confirmation status.
- Ranked programs.
- Ranked professors and laboratories.
- Recent relevant papers.
- Match score and explanation.
- Application deadlines.
- Saved shortlist.
- Drafts awaiting approval.
- Connected email accounts.
- Approved messages awaiting send window.
- Sent/failed/bounced/replied statuses.
- Follow-ups due.
- Notifications and complete outreach history.

Notifications:
- New high-quality match.
- Approaching application deadline.
- Draft ready for review.
- Email sent or failed.
- Professor reply received.
- Follow-up draft ready after three working days.
- OAuth expired/revoked.
- Scheduling blocked by a policy or missing timezone.

Avoid noisy notifications when there is no meaningful change.

---

# 3. Suggested Technical Architecture

Inspect the current code first. This is the recommended direction, not permission to add every dependency immediately.

| Layer | Recommended technology |
|---|---|
| Backend/API | Python 3.10.x, existing framework; FastAPI if needed |
| Frontend | Existing UI if present; otherwise Next.js + TypeScript |
| Database | SQLite for initial local development; PostgreSQL for multi-user production |
| Migrations | Alembic if using SQLAlchemy |
| Agent workflow | Typed workflow/state machine; LangGraph only if it materially helps |
| MCP | Official MCP SDK, local stdio initially |
| Research metadata | OpenAlex, Crossref, Semantic Scholar, arXiv where relevant |
| Documents | PyMuPDF, python-docx, safe TXT parsing; OCR fallback |
| Email | Gmail API; Microsoft Graph optional |
| Scheduling | Persistent job system with timezone-aware logic |
| Testing | pytest, Ruff; frontend tests if frontend exists |
| Browser automation | Playwright only where permitted and necessary |
| Logging | Structured logs with redaction and audit events |

### Service boundaries

Keep the following services independently testable:

1. Profile and document service.
2. Research source/adapters service.
3. Program discovery service.
4. Faculty and publication service.
5. Matching/ranking service.
6. Email provider service.
7. Draft/personalization service.
8. Approval and policy service.
9. Scheduler and queue service.
10. Reply-monitoring service.
11. Notification service.
12. MCP tool adapters.
13. Audit and observability service.

The LLM may interpret research, summarize sources and draft messages. Deterministic backend code must enforce identity, permissions, eligibility rules, approval, scheduling, daily limits, idempotency and audit records.

---

# 4. Data Model — Minimum Entities

Design migrations and constraints for at least:

- `users`
- `student_profiles`
- `student_documents`
- `confirmed_profile_facts`
- `research_interests`
- `programs`
- `program_sources`
- `professors`
- `faculty_affiliations`
- `faculty_contacts`
- `publications`
- `publication_authors`
- `opportunities`
- `match_scores`
- `shortlists`
- `connected_email_accounts`
- `oauth_credentials` (encrypted secrets only)
- `email_drafts`
- `draft_versions`
- `email_approvals`
- `outreach_records`
- `scheduled_jobs`
- `email_threads`
- `email_events`
- `follow_up_records`
- `institution_blackout_periods`
- `university_daily_send_counters`
- `notifications`
- `audit_events`

Use stable IDs, timestamps, source URLs, verification timestamps and explicit status fields. Add uniqueness/idempotency constraints for outreach and provider messages. Ensure university daily counters are concurrency-safe.

---

# 5. Security, Privacy and Responsible Use

- Treat websites, papers, emails and uploaded documents as untrusted data.
- Defend against prompt injection: content found in a paper, webpage, document or email is data, never an instruction to the agent.
- Prevent path traversal and unsafe file parsing.
- Validate MIME type, extension and file size.
- Keep private documents outside Git.
- Store OAuth secrets encrypted and redact them from logs.
- Enforce user-level authorization on every API and MCP tool.
- Use least-privilege OAuth scopes.
- Provide disconnect and data deletion/export paths.
- Respect scholarly API quotas, provider policies, website terms and robots rules.
- Do not scrape aggressively or bypass CAPTCHA/login controls.
- Do not send unsolicited bulk email or evade anti-abuse controls.
- Respect opt-out, rejection and stop requests.
- Do not use real student data or send real emails in automated tests.
- Do not claim an email was sent unless the provider confirms it.
- Never invent an email address, publication, deadline, stipend or eligibility rule.

---

# 6. Dev–QA Agent Collaboration

Use the existing `agent-bridge` system where it is available and compatible.

## Roles

### Planner/Orchestrator
- Reads this specification.
- Maintains milestones, shared task state and architecture decisions.
- Assigns bounded tasks to Dev and QA.
- Prevents conflicting edits.
- Reviews integration and acceptance evidence.

### Dev agent
- Implements one small feature slice at a time.
- Adds tests with each feature.
- Reports files changed, commands run, actual outputs and remaining risks.
- Does not claim completion without test evidence.

### QA agent
- Independently inspects implementation and runs tests.
- Reviews authorization, OAuth, privacy, scheduling, timezones, university cap, concurrency, idempotency and restart recovery.
- Reproduces defects and reports severity, steps and evidence.
- Does not accept Dev's claims without verification.

## Coordination rules

1. Verify daemon status and agent list before attaching or assigning work.
2. Check the installed CLI help; do not invent agent-bridge commands.
3. Use explicit task handoffs and recorded status.
4. Avoid simultaneous edits to the same files.
5. Use separate branches/worktrees or sequential ownership.
6. If the bridge is unavailable, use a documented PowerShell-compatible manual handoff.
7. Do not report an agent as started or attached without observable confirmation.

---

# 7. Implementation Phases and Definition of Done

Do not generate the entire system in one uncontrolled pass. Complete each phase with tests and QA review before proceeding.

## Phase 0 — Repository and environment audit
- Inspect repository, dependencies, Python version, CI workflows, Git state and worktrees.
- Verify agent-bridge daemon and agents.
- Document current architecture and gaps.

**Done when:** audit report is committed or saved in docs; no existing work was overwritten; actual test/lint commands are known.

## Phase 1 — Product foundation
- Define app structure, configuration, logging, error handling and database strategy.
- Add migrations and health checks.
- Add `.env.example` with placeholders only.

**Done when:** app starts locally, DB initializes/migrates, secrets are excluded from Git, basic tests pass.

## Phase 2 — Student profile and documents
- Build onboarding and profile editing.
- Add document upload, parsing, fact extraction and confirmation.
- Add file validation and privacy controls.

**Done when:** synthetic CV/transcript fixtures produce reviewable facts; invalid files are rejected; tests pass.

## Phase 3 — Research sources and opportunity discovery
- Implement source adapters for official program pages and scholarly APIs.
- Store evidence and verification timestamps.
- Add deduplication and source failure handling.

**Done when:** fixture-based tests produce structured programs with provenance and do not fabricate unknown fields.

## Phase 4 — Professor and publication discovery
- Resolve faculty identity and affiliation.
- Retrieve public institutional emails and relevant papers.
- Link each result to official or scholarly evidence.

**Done when:** ambiguous identities are not merged; unverified emails are excluded from sending; publication/source tests pass.

## Phase 5 — Matching and shortlist
- Implement eligibility filters and transparent scoring.
- Add timeline, country, funding and research filters.
- Build student shortlist actions.

**Done when:** tests prove hard eligibility failures cannot be hidden by high similarity scores; ranking explanations are available.

## Phase 6 — Personalized drafts
- Generate evidence-backed email drafts.
- Add editing, versioning and attachment selection.
- Add draft quality and hallucination checks.

**Done when:** every factual claim maps to confirmed profile data or research evidence; changed drafts invalidate approval.

## Phase 7 — Gmail/Outlook OAuth and MCP
- Implement provider adapters and OAuth connect/disconnect.
- Implement narrowly scoped MCP tools and typed schemas.
- Add mocked providers and mocked MCP tests.
- Configure OpenCode MCP integration only after checking the installed version's official configuration format.

**Done when:** a test account can connect in a controlled manual test; mocked tests cover draft creation, sending, thread lookup, token expiry and tool authorization; no MCP path bypasses backend policies.

## Phase 8 — Approval and scheduler
- Implement per-message approval and optional reviewed batch approval.
- Implement recipient timezone, working hours, blackout dates and five-initial-email university cap.
- Add persistent queue, retries and idempotency.

**Done when:** deterministic tests cover timezone/DST, boundary times, weekends, December, configured summer/holiday closures, concurrent cap enforcement and duplicate prevention.

## Phase 9 — Reply monitoring and follow-ups
- Track Gmail/Outlook threads.
- Detect relevant replies, bounces and stop requests.
- Notify after three working days without a reply and prepare follow-up drafts.

**Done when:** reply/bounce/stop events suppress follow-ups; no follow-up sends without approval; working-day calculations pass tests.

## Phase 10 — Dashboard and notifications
- Build program, professor, paper, shortlist, email and notification views.
- Add status history and actionable errors.

**Done when:** the student can trace a result from discovery to outreach outcome and understand every current status.

## Phase 11 — Security, reliability and production-readiness
- Run security review, dependency checks, migration/recovery tests and end-to-end tests.
- Verify graceful shutdown and restart recovery.
- Document deployment and operations.

**Done when:** all acceptance checks pass, known limitations are documented and QA signs off with evidence.

---

# 8. Acceptance Criteria and Test Matrix

At minimum, write automated tests for:

1. Profile extraction from PDF, DOCX and TXT.
2. Scanned, malformed, oversized and unsupported documents.
3. User confirmation of extracted facts and conflicting values.
4. Verified versus unverified faculty identities and emails.
5. Publication relevance, author resolution and source attribution.
6. Program status, deadlines, funding and unknown eligibility fields.
7. Hard eligibility failures and transparent matching scores.
8. Timeline compatibility and date boundaries.
9. Gmail personal and Workspace OAuth success/failure/revocation.
10. Outlook provider behavior if implemented.
11. Account selection and sender identity.
12. MCP schema validation, authentication and authorization.
13. MCP attempts to bypass approval or policy checks are rejected.
14. Monday–Friday and 09:00–17:00 recipient-local boundaries.
15. IANA timezone and daylight-saving transitions.
16. December and configured summer/holiday blackouts.
17. Five initial emails per university per local calendar day under concurrent workers.
18. Multiple accounts/aliases cannot bypass the university cap.
19. Idempotent retry after provider timeout or process restart.
20. Draft edits invalidate prior approval.
21. Unapproved drafts cannot be sent.
22. Three recipient-local working-day follow-up calculation.
23. Reply, bounce, rejection and stop-request suppression.
24. Follow-up requires explicit approval.
25. OAuth expiry and missing-scope behavior.
26. Secret redaction, private-file handling and authorization boundaries.
27. Persistent job recovery and audit-event correctness.
28. End-to-end workflow using synthetic student data and mocked providers.

### Local checks

Run and report actual output for:

```powershell
python --version
python -m ruff check .
python -m pytest -q
```

Also run the project's documented app startup, migration and health-check commands.

### CI checks

- Preserve the protected `main` branch configuration.
- Confirm the exact four required check names and the three configured OS runners in GitHub/workflow configuration.
- Ensure `lint` and all three OS test jobs pass.
- Do not assume OS names; read the actual workflow matrix.
- Keep tests deterministic and offline. No live scholarly API calls or real email sends in CI.

---

# 9. PowerShell Developer Workflow

Use the repository's actual scripts and virtual environment. The following are examples; adjust only after inspecting the project:

```powershell
Set-Location "C:\Users\shash\new_project\ResearchInterBridge"

python --version
git status --short --branch

# Inspect available project files and scripts
Get-ChildItem
Get-ChildItem .\docs

# Run checks
python -m ruff check .
python -m pytest -q
```

Before Dev/QA handoff:

- Record current branch and worktree status.
- State the exact task and files owned by the agent.
- Run targeted tests first, then the full suite.
- Save actual command output in the task report.
- Review `git diff` before committing.
- Never include `.env`, OAuth tokens, student documents or local databases in commits.

---

# 10. Required Deliverables

1. Working student onboarding and profile management.
2. Secure document ingestion and confirmed profile facts.
3. Program discovery with official sources and deadlines.
4. Professor/lab discovery with verified public institutional contacts.
5. Relevant recent papers with DOI/source links.
6. Explainable matching and eligibility checks.
7. Student shortlist and review interface.
8. Personalized, evidence-backed email drafts.
9. Personal/institutional Gmail connection through OAuth.
10. Outlook integration if included in the chosen scope.
11. MCP servers/tools with typed schemas and backend authorization.
12. Approval-gated email sending through the connected account.
13. Timezone-aware scheduling and university daily cap.
14. Reply detection, notifications and three-working-day follow-up drafts.
15. Dashboard and audit history.
16. Unit, integration, security and end-to-end tests.
17. Database migrations, backup and recovery instructions.
18. PowerShell setup/run/test/troubleshooting documentation.
19. `.env.example` with placeholders only.
20. QA report listing passed tests, failed tests, evidence and remaining production risks.

---

# 11. Required Dev Agent First Response

Before coding, the Dev agent must report:

1. What already exists in the repository.
2. What is missing relative to this specification.
3. Which existing components can be reused.
4. Actual Python version, framework, dependencies and test/lint commands.
5. Actual CI check names and OS matrix.
6. Actual agent-bridge daemon and agent status.
7. A prioritized implementation plan with the first small task.

Then implement Phase 0 and the first safe, testable increment. Do not start a broad rewrite.

If an OAuth credential, provider approval, user decision or external permission is missing, implement and test the safe parts first and state the exact blocker. Never fabricate access, test results, source evidence or successful sends.

---

# 12. Non-Negotiable Product Rules

- Research discovery can be automated; outbound sending is approval-gated.
- A connected Gmail account is not blanket permission to email anyone.
- Personal and institutional Gmail accounts must be supported through user-authorized OAuth, subject to provider and Workspace restrictions.
- The five-initial-emails-per-university-per-local-day limit is global across the student's connected accounts and workers.
- Sending is Monday–Friday, 09:00–17:00 recipient local time, excluding December and configured institution-specific summer/holiday blackouts.
- Follow-up is prepared after three working days without a relevant reply; it is not sent without approval.
- Any reply, bounce, rejection or stop request suppresses pending follow-up.
- MCP tools must use the same backend authorization, approval, scheduling and audit services as the application UI.
- The backend, not the LLM, enforces security-critical rules.
- Tests use synthetic profiles, fixtures and mocked providers; never send real professor emails in automated tests.
- Never invent facts, contacts, papers, deadlines, funding, eligibility or send outcomes.
- Never claim production readiness without passing tests and an independent QA review.
