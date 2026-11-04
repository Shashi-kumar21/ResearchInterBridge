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
        <ConsentManager />
        <p className="fine-print">
          Gmail access stays unused until the OAuth connection exists in a later phase. After that,
          revoking it and deleting the account will be available from the site; today deletion is an
          HTTP API call.
        </p>
      </main>
    </>
  );
}
