import type { Metadata } from "next";

import { LoginForm } from "@/components/login-form";
import { SiteHeader } from "@/components/site-header";

export const metadata: Metadata = {
  title: "Sign in",
  description:
    "Sign in to ResearchInterBridge to review your consent choices. No email is sent by this site.",
};

export default function LoginPage() {
  return (
    <>
      <SiteHeader />
      <main id="main" className="page">
        <p className="eyebrow">Account</p>
        <h1>Sign in</h1>
        <p className="page-lead">
          Sign in with the email and password you registered. A wrong password and an unknown email
          get the same answer; repeated failures lock the account for 15 minutes.
        </p>
        <noscript>
          <p className="alert">
            Signing in needs JavaScript. The form sends your credentials to the API directly;
            without JavaScript the button stays disabled so nothing is ever submitted.
          </p>
        </noscript>
        <LoginForm />
        <p className="fine-print">
          Nothing is sent to a professor from this page or from this site. Outreach needs the Gmail
          connection and per-draft approval, neither of which exists yet.
        </p>
      </main>
    </>
  );
}
