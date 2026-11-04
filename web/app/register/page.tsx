import type { Metadata } from "next";

import { RegisterForm } from "@/components/register-form";
import { SiteHeader } from "@/components/site-header";

export const metadata: Metadata = {
  title: "Create your account",
  description:
    "Create a ResearchInterBridge account, read the consent texts and agree only to what you need. No email is sent by this site.",
};

export default function RegisterPage() {
  return (
    <>
      <SiteHeader />
      <main id="main" className="page">
        <p className="eyebrow">Account</p>
        <h1>Create your account</h1>
        <p className="page-lead">
          The account stores your email, a salted password hash and the consents you tick below.
          The browser never receives the hash, and the session cookie is HttpOnly.
        </p>
        <noscript>
          <p className="alert">
            Creating an account needs JavaScript: the page loads the consent texts from the API and
            keeps the button disabled until it can. Nothing is submitted without it.
          </p>
        </noscript>
        <RegisterForm />
        <p className="fine-print">
          Exporting your account data and deleting the account are available through the HTTP API
          today; buttons for them are not built yet. Deletion asks for your password again, removes
          your profile, documents (files included) and sessions, and can be retried if a file is
          still open in another program. This site cannot send email.
        </p>
      </main>
    </>
  );
}
