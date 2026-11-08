import type { Metadata } from "next";

import { ResearchBrowser } from "@/components/research-browser";
import { SiteHeader } from "@/components/site-header";

export const metadata: Metadata = {
  title: "Research discovery",
  description:
    "Browse cached universities, departments and research programs with their source link, last-checked date and a stale flag. No record is invented.",
};

export default function ResearchPage() {
  return (
    <>
      <SiteHeader />
      <main id="main" className="page">
        <p className="eyebrow">Research discovery</p>
        <h1>Browse the shared research cache</h1>
        <p className="page-lead">
          Start with an institution, then narrow to a department and a program. Every row keeps
          the source it came from and the date it was last checked; an unstated field stays
          unstated. Browsing reads the cache only, so it still works when a source is unavailable.
        </p>
        <noscript>
          <p className="alert">
            This page needs JavaScript to read the shared research cache through the API. No
            institution or program can be listed without it.
          </p>
        </noscript>
        <ResearchBrowser />
      </main>
    </>
  );
}
