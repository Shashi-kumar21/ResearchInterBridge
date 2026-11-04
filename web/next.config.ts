import type { NextConfig } from "next";

const nextConfig: NextConfig = {
  poweredByHeader: false,
  reactStrictMode: true,
  // The site serves no user-uploaded HTML and embeds nothing, so a strict
  // header set costs nothing and protects the pages that will show scraped
  // third-party text (professors, papers) in later phases.
  async headers() {
    return [
      {
        source: "/:path*",
        headers: [
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          {
            key: "Permissions-Policy",
            value: "camera=(), microphone=(), geolocation=()",
          },
          {
            // Directive subset that needs no nonce. `script-src` is deliberately
            // absent: Next emits inline bootstrap scripts, so it needs a nonce
            // minted per request when the dynamic pages land (Phase 2d-2), not a
            // blanket 'unsafe-inline' pasted here today. `form-action 'none'`
            // blocks every native form submission; the account forms submit with
            // fetch, so a pre-hydration click can never put a password in a URL.
            key: "Content-Security-Policy",
            value:
              "base-uri 'self'; object-src 'none'; frame-ancestors 'none'; form-action 'none'",
          },
        ],
      },
    ];
  },
};

export default nextConfig;
