/**
 * Small display helpers for server values.
 *
 * They deliberately avoid `toLocaleString`: the page must render the same text
 * on every machine, and the tests should not depend on the CI locale.
 */

/** "912 B", "4.2 KiB", "1.1 MiB" — binary units, one decimal at most. */
export function formatBytes(bytes: number): string {
  if (!Number.isFinite(bytes) || bytes < 0) {
    return "unknown size";
  }
  if (bytes < 1024) {
    return `${Math.round(bytes)} B`;
  }
  const units = ["KiB", "MiB", "GiB"];
  let value = bytes / 1024;
  let unit = units[0];
  for (let index = 1; index < units.length && value >= 1024; index += 1) {
    value /= 1024;
    unit = units[index];
  }
  const rounded = value >= 10 ? Math.round(value) : Math.round(value * 10) / 10;
  return `${rounded} ${unit}`;
}

/** The date part of an ISO timestamp, or the original text if it is not one. */
export function formatDate(value: string | null): string {
  if (!value) {
    return "not recorded";
  }
  const parsed = new Date(value);
  if (Number.isNaN(parsed.getTime())) {
    return value;
  }
  return parsed.toISOString().slice(0, 10);
}
