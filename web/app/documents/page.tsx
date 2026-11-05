import type { Metadata } from "next";

import { DocumentManager } from "@/components/document-manager";
import { SiteHeader } from "@/components/site-header";

export const metadata: Metadata = {
  title: "Documents",
  description:
    "Upload a CV or transcript, see what the server stored, and extract candidate facts for review.",
};

export default function DocumentsPage() {
  return (
    <>
      <SiteHeader />
      <main id="main" className="page">
        <p className="eyebrow">Documents</p>
        <h1>CVs and transcripts</h1>
        <p className="page-lead">
          The original file is stored unchanged and the extracted text only proposes facts. A
          failed parse keeps the document and its reason, so a scan can be replaced without
          losing the record.
        </p>
        <noscript>
          <p className="alert">
            This page needs JavaScript to upload and list documents through the API. No file can
            be uploaded from this page without it.
          </p>
        </noscript>
        <DocumentManager />
      </main>
    </>
  );
}
