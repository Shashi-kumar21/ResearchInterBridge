import type { Metadata } from "next";

import { FactReview } from "@/components/fact-review";
import { SiteHeader } from "@/components/site-header";

export const metadata: Metadata = {
  title: "Fact review",
  description:
    "Confirm, correct or reject every fact extracted from your documents before it can be used in an email.",
};

export default function FactsPage() {
  return (
    <>
      <SiteHeader />
      <main id="main" className="page">
        <p className="eyebrow">Fact review</p>
        <h1>Confirm what is true</h1>
        <p className="page-lead">
          Extraction proposes; you decide. A confirmed fact is the only truth for its field, and
          only confirmed facts may be copied to your profile or used in a draft.
        </p>
        <noscript>
          <p className="alert">
            This page needs JavaScript to read and confirm facts through the API. No fact can be
            confirmed from this page without it.
          </p>
        </noscript>
        <FactReview />
      </main>
    </>
  );
}
