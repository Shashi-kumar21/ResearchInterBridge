import type { Metadata } from "next";

import { ProfileForm } from "@/components/profile-form";
import { SiteHeader } from "@/components/site-header";

export const metadata: Metadata = {
  title: "Onboarding and profile",
  description:
    "Save the academic profile research discovery will use: degree, institution, coursework, skills, research interests, target countries and internship dates.",
};

export default function OnboardingPage() {
  return (
    <>
      <SiteHeader />
      <main id="main" className="page">
        <p className="eyebrow">Onboarding</p>
        <h1>Your academic profile</h1>
        <p className="page-lead">
          Discovery uses what you confirm here. The form saves the profile as a whole, and the
          completeness line shows what matching still cannot see. Nothing is inferred from a
          blank field.
        </p>
        <noscript>
          <p className="alert">
            This form needs JavaScript to read and save your profile through the API. No profile
            can be saved from this page without it.
          </p>
        </noscript>
        <ProfileForm />
      </main>
    </>
  );
}
