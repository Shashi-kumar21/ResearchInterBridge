import { describe, expect, it } from "vitest";

import {
  CAPABILITIES,
  LIMITS,
  STATUS_LABELS,
  WORKFLOW,
  hasWebCapability,
  summaryCounts,
  surfaceLabel,
  type Capability,
  type CapabilityStatus,
  type CapabilitySurface,
} from "./roadmap";

const STATUSES: readonly CapabilityStatus[] = ["available", "in-progress", "planned"];
const SURFACES: readonly CapabilitySurface[] = ["cli", "api", "web"];

describe("capability model", () => {
  it("uses unique ids", () => {
    const ids = CAPABILITIES.map((capability) => capability.id);

    expect(new Set(ids).size).toBe(ids.length);
  });

  it("gives every capability a real title and a specific detail", () => {
    for (const capability of CAPABILITIES) {
      expect(capability.title.length).toBeGreaterThan(3);
      expect(capability.detail.length).toBeGreaterThan(40);
    }
  });

  it("only uses known statuses and surfaces", () => {
    for (const capability of CAPABILITIES) {
      expect(STATUSES).toContain(capability.status);
      expect(SURFACES).toContain(capability.surface);
    }
  });

  it("never labels email sending as available", () => {
    const sending = CAPABILITIES.find((capability) => capability.id === "sending");

    expect(sending).toBeDefined();
    expect(sending?.status).not.toBe("available");
  });

  it("marks the shipped account screens as available on the web", () => {
    // Flipped deliberately in Phase 2d-3b: register, sign-in and consent really
    // run on this site now. The canary is the specific card, not "any card".
    const shell = CAPABILITIES.find((capability) => capability.id === "web-shell");

    expect(shell).toBeDefined();
    expect(shell?.status).toBe("available");
    expect(shell?.surface).toBe("web");
    expect(hasWebCapability()).toBe(true);
  });

  it("detects a web capability that really is available", () => {
    // The negative case above would stay green if the predicate were dead code.
    const shipped: Capability = {
      id: "first-web-feature",
      title: "First web feature",
      detail: "A detail long enough to be plausible for a real capability.",
      status: "available",
      surface: "web",
    };

    expect(hasWebCapability([shipped])).toBe(true);
    expect(hasWebCapability([{ ...shipped, status: "planned" }])).toBe(false);
  });

  it("ignores CLI capabilities when detecting the web surface", () => {
    const cliOnly: Capability = {
      id: "cli-only",
      title: "CLI only",
      detail: "A detail long enough to be plausible for a real capability.",
      status: "available",
      surface: "cli",
    };

    expect(hasWebCapability([cliOnly])).toBe(false);
  });

  it("labels every surface line to agree with its status badge", () => {
    for (const capability of CAPABILITIES) {
      const label = surfaceLabel(capability);

      expect(label.startsWith(STATUS_LABELS[capability.status])).toBe(
        capability.status !== "available",
      );
      if (capability.status !== "available") {
        expect(label).not.toMatch(/^Available on/);
      }
    }
  });

  it("never tells a visitor that a planned feature is available", () => {
    const planned = CAPABILITIES.filter((capability) => capability.status === "planned");

    expect(planned.length).toBeGreaterThan(0);
    for (const capability of planned) {
      expect(surfaceLabel(capability)).toMatch(/^Planned for: /);
    }
  });

  it("labels each status and surface with a readable word", () => {
    for (const status of STATUSES) {
      expect(STATUS_LABELS[status].length).toBeGreaterThan(2);
    }
    for (const capability of CAPABILITIES) {
      expect(surfaceLabel(capability)).toMatch(/the (command line|HTTP API|website)$/);
    }
  });

  it("keeps each available capability on the surface it really runs on", () => {
    const expected: Record<string, CapabilitySurface> = {
      account: "api",
      profile: "cli",
      documents: "cli",
      facts: "cli",
      "web-shell": "web",
    };

    for (const [id, surface] of Object.entries(expected)) {
      const capability = CAPABILITIES.find((item) => item.id === id);

      expect(capability?.status, id).toBe("available");
      expect(capability?.surface, id).toBe(surface);
      expect(surfaceLabel(capability as Capability)).toMatch(/^Available on: /);
    }
  });
});

describe("summaryCounts", () => {
  it("counts each status", () => {
    expect(summaryCounts()).toEqual({ available: 5, inProgress: 0, planned: 6 });
  });

  it("counts the whole list", () => {
    const counts = summaryCounts();
    const total = counts.available + counts.inProgress + counts.planned;

    expect(total).toBe(CAPABILITIES.length);
  });

  it("handles an empty list", () => {
    expect(summaryCounts([])).toEqual({ available: 0, inProgress: 0, planned: 0 });
  });

  it("counts a list of one", () => {
    const only: Capability = {
      id: "x",
      title: "X",
      detail: "A detail long enough to be useful.",
      status: "planned",
      surface: "web",
    };

    expect(summaryCounts([only])).toEqual({ available: 0, inProgress: 0, planned: 1 });
  });
});

describe("honesty guards", () => {
  it("states that sending runs from the student's own Gmail", () => {
    const gmail = LIMITS.find((limit) => limit.id === "gmail");
    const text = `${gmail?.title ?? ""} ${gmail?.detail ?? ""}`;

    expect(text).toMatch(/your own account/i);
    expect(text).toMatch(/approve/i);
  });

  it("states that the product is free without hiding provider limits", () => {
    const ids = LIMITS.map((limit) => limit.id);

    expect(ids).toContain("free");
    expect(ids).toContain("provider-limits");
  });

  it("describes the workflow in a fixed, ordered shape", () => {
    expect(WORKFLOW.map((step) => step.id)).toEqual([
      "describe",
      "confirm",
      "discover",
      "prepare",
      "approve",
    ]);
    for (const step of WORKFLOW) {
      expect(step.detail.length).toBeGreaterThan(10);
    }
  });
});

describe("copy honesty", () => {
  it("does not claim that document contents can be exported", () => {
    const account = CAPABILITIES.find((capability) => capability.id === "account");
    const detail = account?.detail ?? "";

    expect(detail).not.toMatch(/everything you uploaded/i);
    expect(detail).toMatch(/account data/i);
  });

  it("names the HTTP API as the surface for export and deletion", () => {
    const account = CAPABILITIES.find((capability) => capability.id === "account");

    expect(account?.surface).toBe("api");
    expect(surfaceLabel(account as Capability)).toBe("Available on: the HTTP API");
  });
});
