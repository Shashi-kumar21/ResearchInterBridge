import type { Metadata } from "next";

import { DraftStudio } from "@/components/draft-studio";
import { SiteHeader } from "@/components/site-header";

export const metadata: Metadata = {
  title: "Email drafts",
  description:
    "One draft per shortlisted professor, built from verified facts, checked for deliverability and approved by you, exactly as written. Nothing is sent from here.",
};

export default function DraftsPage() {
  return (
    <>
      <SiteHeader />
      <main id="main" className="page">
        <p className="eyebrow">Email drafts</p>
        <h1>Write each email, then approve it</h1>
        <p className="page-lead">
          Each draft starts from verified facts only: the professor&apos;s paper or topic, your
          confirmed profile and your own CV lines. Edit freely, fix anything the checks flag, then
          approve the exact version. Any later change clears the approval. Nothing is sent yet.
        </p>
        <noscript>
          <p className="alert">This page needs JavaScript to load your drafts through the API.</p>
        </noscript>
        <DraftStudio />
      </main>
    </>
  );
}
