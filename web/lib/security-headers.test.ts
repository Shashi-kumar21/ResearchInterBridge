import { describe, expect, it } from "vitest";

import nextConfig from "../next.config";

/** The header value for one key, or an empty string when it is absent. */
async function header(key: string): Promise<string> {
  const groups = (await nextConfig.headers?.()) ?? [];
  const first = groups[0];

  return first?.headers.find((entry) => entry.key === key)?.value ?? "";
}

describe("security headers", () => {
  it("blocks native form submissions everywhere", async () => {
    // The account forms submit with fetch. A pre-hydration click must not fall
    // back to a native GET, which would put a password in the URL, so the
    // browser refuses every native form submission as a second line of defence.
    const csp = await header("Content-Security-Policy");

    expect(csp).toContain("form-action 'none'");
    expect(csp).not.toContain("form-action 'self'");
  });

  it("keeps the nonce-free directives in place", async () => {
    const csp = await header("Content-Security-Policy");

    expect(csp).toContain("base-uri 'self'");
    expect(csp).toContain("object-src 'none'");
    expect(csp).toContain("frame-ancestors 'none'");
    // A blanket script-src would hide the missing nonce, so it must stay absent.
    expect(csp).not.toContain("script-src");
    expect(await header("X-Content-Type-Options")).toBe("nosniff");
    expect(await header("X-Frame-Options")).toBe("DENY");
  });
});
