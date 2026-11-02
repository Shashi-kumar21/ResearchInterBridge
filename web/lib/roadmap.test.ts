import { describe, expect, it } from "vitest";

import {
  CAPABILITIES,
  LIMITS,
  WORKFLOW,
  hasWebCapability,
  summaryCounts,
  type Capability,
  type CapabilityStatus,
  type CapabilitySurface,
} from "./roadmap";

const STATUSES: readonly CapabilityStatus[] = ["available", "in-progress", "planned"];
const SURFACES: readonly CapabilitySurface[] = ["cli", "web"];

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

  it("does not claim any capability runs in the browser yet", () => {
    // Canary for Phase 2d-1: the only shipped surface is the Python CLI, so the
    // site must not imply that a feature works on this website. Flip this test
    // deliberately with the first real web feature.
    expect(hasWebCapability()).toBe(false);
  });

  it("keeps every available capability on the CLI surface", () => {
    const available = CAPABILITIES.filter((capability) => capability.status === "available");

    expect(available.length).toBeGreaterThan(0);
    for (const capability of available) {
      expect(capability.surface).toBe("cli");
    }
  });
});

describe("summaryCounts", () => {
  it("counts each status", () => {
    expect(summaryCounts()).toEqual({ available: 4, inProgress: 1, planned: 6 });
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
