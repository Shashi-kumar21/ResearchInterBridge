import type { Metadata } from "next";

import { ConsentManager } from "@/components/consent-manager";
import { SiteHeader } from "@/components/site-header";

export const metadata: Metadata = {
  title: "Consent and privacy settings",
  description:
    "Review what you agreed to, when the policy version changes, and grant optional consents such as Gmail access before it is used.",
};

export default function ConsentPage() {
  return (
    <>
      <SiteHeader />
      <main id="main" className="page">
        <p className="eyebrow">Privacy</p>
        <h1>Consent and privacy settings</h1>
        <p className="page-lead">
          Consent is recorded per kind and per policy version. This page shows the server&apos;s
          ledger for your account, not a browser preference, so it survives a different device or a
          cleared cookie jar.
        </p>
        <noscript>
          <p className="alert">
            This page needs JavaScript to read your consent ledger from the API. No consent can be
            granted from a page that cannot load it.
          </p>
        </noscript>
        <ConsentManager />
        <p className="fine-print">
          Gmail access is recorded now but stays unused until the OAuth connection exists in a later
          phase. This site cannot connect to, revoke or send from Gmail yet. Deleting the account is
          an HTTP API call today; buttons for it are not built.
        </p>
      </main>
    </>
  );
}
