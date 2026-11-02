/**
 * What the product can actually do right now, rendered by the landing page.
 *
 * The site is the public face of an in-progress product, so the copy is data
 * here instead of prose in JSX: `roadmap.test.ts` asserts the invariants that
 * keep it honest (nothing unbuilt is labelled available, nothing is claimed to
 * run in the browser while the only surface is the Python CLI).
 */

export type CapabilityStatus = "available" | "in-progress" | "planned";

/** Where a capability can be used today. */
export type CapabilitySurface = "cli" | "web";

export interface Capability {
  readonly id: string;
  readonly title: string;
  readonly detail: string;
  readonly status: CapabilityStatus;
  readonly surface: CapabilitySurface;
}

export const STATUS_LABELS: Record<CapabilityStatus, string> = {
  available: "Working today",
  "in-progress": "In progress",
  planned: "Planned",
};

/** What the badge and the "available on" line call each surface. */
export const SURFACE_LABELS: Record<CapabilitySurface, string> = {
  cli: "the command line",
  web: "the website",
};

export interface CapabilityCounts {
  readonly available: number;
  readonly inProgress: number;
  readonly planned: number;
}

export const CAPABILITIES: readonly Capability[] = [
  {
    id: "account",
    title: "Accounts, consent and privacy controls",
    detail:
      "Registration records versioned consent before anything is stored. " +
      "Passwords are kept as salted scrypt hashes, five wrong attempts lock an " +
      "account for 15 minutes, and sessions expire. The Python service can " +
      "export or delete everything you uploaded; CLI commands for those two " +
      "land with the HTTP API.",
    status: "available",
    surface: "cli",
  },
  {
    id: "profile",
    title: "Academic profile",
    detail:
      "One validated profile per student — degree, year, coursework, skills, " +
      "research keywords, target countries and internship dates. Every read is " +
      "scoped to the owner.",
    status: "available",
    surface: "cli",
  },
  {
    id: "documents",
    title: "CV and transcript intake",
    detail:
      "PDF, DOCX and TXT are checked by content rather than extension, capped at " +
      "10 MB, and stored under a hash-named file in the student's own directory. " +
      "Malformed or mismatched files are rejected at upload; a scan or a " +
      "password-protected PDF is kept but reported as failed with the reason " +
      "(OCR is not available yet).",
    status: "available",
    surface: "cli",
  },
  {
    id: "facts",
    title: "Fact confirmation",
    detail:
      "Extracted facts stay pending until you confirm or correct them. When two " +
      "documents disagree the conflict is shown instead of one value silently " +
      "winning, and only confirmed facts may ever appear in a draft.",
    status: "available",
    surface: "cli",
  },
  {
    id: "web-shell",
    title: "Public website foundation",
    detail:
      "This page: responsive, keyboard accessible, no tracking scripts, and " +
      "honest about what is not built yet. Registration and onboarding screens " +
      "arrive with the HTTP API.",
    status: "in-progress",
    surface: "web",
  },
  {
    id: "discovery",
    title: "Research discovery",
    detail:
      "University → department → faculty browse with a source link, a " +
      "last-checked timestamp and an offline cache, so a repeated query is served " +
      "from the database instead of hammering public sources.",
    status: "planned",
    surface: "web",
  },
  {
    id: "matching",
    title: "Explainable matching",
    detail:
      "A score you can inspect, plus hard eligibility gates: if funding, degree " +
      "level or start date do not fit, the group is excluded rather than ranked.",
    status: "planned",
    surface: "web",
  },
  {
    id: "cv-review",
    title: "CV review per research group",
    detail:
      "Suggestions tied to a specific professor, shown as a highlighted diff, " +
      "kept as an approved version. The original upload is never modified.",
    status: "planned",
    surface: "web",
  },
  {
    id: "drafts",
    title: "Draft studio",
    detail:
      "A personalized draft per professor with the evidence it is built from, a " +
      "deterministic deliverability check, and edits that invalidate a previous " +
      "approval.",
    status: "planned",
    surface: "web",
  },
  {
    id: "sending",
    title: "Approval-gated sending through your Gmail",
    detail:
      "Gmail OAuth with least-privilege scopes only; nothing leaves without your " +
      "per-draft approval. Fair-use guards are fixed in the spec: 20 initial " +
      "emails per " +
      "account per day, 90 seconds between sends, five per university per day, a " +
      "warm-up ramp and a suppression list that honours any opt-out.",
    status: "planned",
    surface: "web",
  },
  {
    id: "replies",
    title: "Replies and follow-ups",
    detail:
      "Replies are classified so a positive answer becomes a next step, and a " +
      "follow-up is drafted after three working days — again only with approval.",
    status: "planned",
    surface: "web",
  },
];

/** Fair-use and infrastructure facts the product must not hide. */
export interface ProductLimit {
  readonly id: string;
  readonly title: string;
  readonly detail: string;
}

export const LIMITS: readonly ProductLimit[] = [
  {
    id: "free",
    title: "Free for students",
    detail:
      "No subscription, premium tier or paywall. The project is open source and " +
      "can be self-hosted.",
  },
  {
    id: "provider-limits",
    title: "External services have their own limits",
    detail:
      "Scholarly APIs, AI providers and Google's quotas apply. Free-tier hosting " +
      "is not unlimited, and the project does not promise zero-cost operation.",
  },
  {
    id: "gmail",
    title: "Sending happens from your own account",
    detail:
      "Google's authenticated sending — not this product — is what authorizes " +
      "delivery, and a message is only ever sent after you approve that exact " +
      "draft.",
  },
  {
    id: "no-bulk",
    title: "No bulk or scraped outreach",
    detail:
      "One student, one professor, one approved draft at a time. Contacts and " +
      "papers are never invented, and a stop request is honoured immediately.",
  },
];

/** How the finished workflow will feel, in order. */
export interface WorkflowStep {
  readonly id: string;
  readonly title: string;
  readonly detail: string;
}

export const WORKFLOW: readonly WorkflowStep[] = [
  {
    id: "describe",
    title: "Describe your research interests",
    detail: "A short profile and the documents you already have.",
  },
  {
    id: "confirm",
    title: "Confirm every fact",
    detail: "Extraction proposes; you decide what is true and what a draft may use.",
  },
  {
    id: "discover",
    title: "Browse groups and professors",
    detail: "Every record keeps its source link and last-checked date.",
  },
  {
    id: "prepare",
    title: "Prepare one specific email",
    detail: "Evidence-backed, with a CV version approved for that group.",
  },
  {
    id: "approve",
    title: "Approve, send, track",
    detail: "Sent from your Gmail, inside the daily and per-university caps.",
  },
];

export function summaryCounts(
  capabilities: readonly Capability[] = CAPABILITIES,
): CapabilityCounts {
  let available = 0;
  let inProgress = 0;
  let planned = 0;

  for (const capability of capabilities) {
    if (capability.status === "available") {
      available += 1;
    } else if (capability.status === "in-progress") {
      inProgress += 1;
    } else {
      planned += 1;
    }
  }

  return { available, inProgress, planned };
}

/**
 * The line under each card. It has to agree with the status badge: a planned
 * card must never read "Available on: ...".
 */
export function surfaceLabel(capability: Capability): string {
  const target = SURFACE_LABELS[capability.surface];
  switch (capability.status) {
    case "available":
      return `Available on: ${target}`;
    case "in-progress":
      return `In progress on: ${target}`;
    case "planned":
      return `Planned for: ${target}`;
  }
}

/** True when any capability is already usable in a browser. */
export function hasWebCapability(
  capabilities: readonly Capability[] = CAPABILITIES,
): boolean {
  return capabilities.some(
    (capability) => capability.status === "available" && capability.surface === "web",
  );
}
