import { describe, expect, it } from "vitest";

import { formatBytes, formatDate } from "./format";

describe("formatBytes", () => {
  it("uses bytes below one KiB", () => {
    expect(formatBytes(0)).toBe("0 B");
    expect(formatBytes(912)).toBe("912 B");
  });

  it("uses binary units with at most one decimal", () => {
    expect(formatBytes(1024)).toBe("1 KiB");
    expect(formatBytes(1536)).toBe("1.5 KiB");
    expect(formatBytes(10 * 1024 * 1024)).toBe("10 MiB");
  });

  it("does not pretend to know an invalid size", () => {
    expect(formatBytes(Number.NaN)).toBe("unknown size");
    expect(formatBytes(-1)).toBe("unknown size");
  });
});

describe("formatDate", () => {
  it("keeps only the date of an ISO timestamp", () => {
    expect(formatDate("2026-11-04T09:30:00Z")).toBe("2026-11-04");
  });

  it("names a missing date instead of rendering an empty value", () => {
    expect(formatDate(null)).toBe("not recorded");
  });

  it("leaves unrecognised text alone", () => {
    expect(formatDate("not a date")).toBe("not a date");
  });
});
