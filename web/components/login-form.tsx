"use client";

// Plain anchors are deliberate: see the note in register-form.tsx.
/* eslint-disable @next/next/no-html-link-for-pages */

import { useState, type FormEvent } from "react";

import { loginAccount, type SessionResponse } from "@/lib/api";
import { describeApiError } from "@/lib/errors";
import { useHydrated } from "@/lib/hydration";

/**
 * Sign-in form.
 *
 * A wrong password and an unknown email get the same sentence because the API
 * returns the same body for both. A lockout or the route's own rate limit answers
 * `429`, and the API sends `Retry-After`; the message quotes it so the student
 * knows whether to wait or to reset the password.
 */
export function LoginForm() {
  const hydrated = useHydrated();
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [session, setSession] = useState<SessionResponse | null>(null);

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      setSession(await loginAccount({ email, password }));
      setPassword("");
    } catch (failure) {
      setError(describeApiError(failure));
    } finally {
      setSubmitting(false);
    }
  }

  if (session) {
    return (
      <div className="panel" role="status">
        <h2>Signed in</h2>
        <p>
          You are signed in as <strong>{session.account.email}</strong>. Your session expires at{" "}
          <time dateTime={session.expires_at}>{session.expires_at}</time>.
        </p>
        <div className="button-row">
          <a className="button" href="/consent">
            Consent and privacy settings
          </a>
          <a className="button button-quiet" href="/">
            Back to the start page
          </a>
        </div>
      </div>
    );
  }

  return (
    <form className="panel" method="post" onSubmit={onSubmit}>
      {error ? (
        <p className="alert" role="alert">
          {error}
        </p>
      ) : null}

      <div className="field">
        <label htmlFor="login-email">Email address</label>
        <input
          id="login-email"
          name="email"
          type="email"
          autoComplete="email"
          required
          value={email}
          onChange={(event) => setEmail(event.target.value)}
        />
      </div>

      <div className="field">
        <label htmlFor="login-password">Password</label>
        <input
          id="login-password"
          name="password"
          type="password"
          autoComplete="current-password"
          required
          value={password}
          onChange={(event) => setPassword(event.target.value)}
        />
      </div>

      <div className="button-row">
        <button className="button" type="submit" disabled={!hydrated || submitting}>
          {submitting ? "Signing in…" : "Sign in"}
        </button>
        <a href="/register">Create an account instead</a>
      </div>
    </form>
  );
}
