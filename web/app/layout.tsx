import type { Metadata, Viewport } from "next";
import type { ReactNode } from "react";

import "./globals.css";

export const metadata: Metadata = {
  title: {
    default: "ResearchInterBridge — research internships abroad, without the spam",
    template: "%s · ResearchInterBridge",
  },
  description:
    "Find research groups and professors abroad, confirm the facts a draft may " +
    "use, and send one approved email at a time from your own Gmail. Free and " +
    "open source; in active development.",
  applicationName: "ResearchInterBridge",
  keywords: [
    "research internship",
    "summer research",
    "professor outreach",
    "research groups",
  ],
  openGraph: {
    title: "ResearchInterBridge",
    description:
      "Discovery and approval-gated outreach for international research internships. " +
      "In active development: no email can be sent yet.",
    type: "website",
  },
  robots: { index: true, follow: true },
};

export const viewport: Viewport = {
  width: "device-width",
  initialScale: 1,
  themeColor: [
    { media: "(prefers-color-scheme: light)", color: "#f6f4ee" },
    { media: "(prefers-color-scheme: dark)", color: "#12161a" },
  ],
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="en">
      <body>
        <a className="skip-link" href="#main">
          Skip to content
        </a>
        {children}
      </body>
    </html>
  );
}
