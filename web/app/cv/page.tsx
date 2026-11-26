import type { Metadata } from "next";

import { CvStudio } from "@/components/cv-studio";
import { SiteHeader } from "@/components/site-header";

export const metadata: Metadata = {
  title: "CV review",
  description:
    "Professor-specific CV suggestions with evidence. Nothing is invented, nothing is applied until you approve it, and your original CV is never changed.",
};

export default function CvPage() {
  return (
    <>
      <SiteHeader />
      <main id="main" className="page">
        <p className="eyebrow">CV review</p>
        <h1>Tailor your CV, one change at a time</h1>
        <p className="page-lead">
          Suggestions only reorder your own lines, add a skill you already confirmed, or point out
          a line with no result. Accept, reject or reword each one, check the before-and-after,
          then approve. Your original CV is never edited.
        </p>
        <noscript>
          <p className="alert">This page needs JavaScript to read your CV through the API.</p>
        </noscript>
        <CvStudio />
      </main>
    </>
  );
}
