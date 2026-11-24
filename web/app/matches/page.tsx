import type { Metadata } from "next";

import { MatchBrowser } from "@/components/match-browser";
import { SiteHeader } from "@/components/site-header";

export const metadata: Metadata = {
  title: "Matches",
  description:
    "Faculty and programs ranked for your profile, with every point explained, failed eligibility shown first and unknowns listed. Shortlist several at once.",
};

export default function MatchesPage() {
  return (
    <>
      <SiteHeader />
      <main id="main" className="page">
        <p className="eyebrow">Matches</p>
        <h1>Ranked for your profile</h1>
        <p className="page-lead">
          Each score shows how it was earned. A failed eligibility condition always puts an entry
          last, and anything a source did not state is listed as unknown. Your shortlist and the
          scores it saves are visible only to you.
        </p>
        <noscript>
          <p className="alert">
            This page needs JavaScript to score matches through the API. No match can be shown
            without it.
          </p>
        </noscript>
        <MatchBrowser />
      </main>
    </>
  );
}
