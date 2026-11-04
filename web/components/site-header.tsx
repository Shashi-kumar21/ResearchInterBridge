import Link from "next/link";

/**
 * Shared header. A server component: the account forms are the only client code
 * on the site, so navigation stays out of the browser bundle.
 */
export function SiteHeader() {
  return (
    <header className="site-header">
      <Link className="brand" href="/">
        ResearchInterBridge
      </Link>
      <span className="phase">Phase 2 · no email can be sent yet</span>
      <nav className="site-nav" aria-label="Account">
        <Link href="/register">Create account</Link>
        <Link href="/login">Sign in</Link>
        <Link href="/consent">Consent</Link>
      </nav>
    </header>
  );
}
