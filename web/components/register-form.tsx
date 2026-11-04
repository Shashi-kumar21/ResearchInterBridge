"use client";

// Plain anchors are deliberate in this client component: they keep the forms
// renderable in tests without the App Router, and these pages are static.

import { useEffect, useState, type FormEvent } from "react";

import {
  fetchConsentCatalogue,
  registerAccount,
  type ConsentCatalogue,
  type SessionResponse,
} from "@/lib/api";
import { describeApiError } from "@/lib/errors";

/**
 * Registration form.
 *
 * Consent texts are fetched from `GET /api/consents/catalogue` instead of being
 * copied here: the wording a student agrees to must be the wording the backend
 * records against a policy version, and a second copy in the site would drift.
 * The submit button stays disabled until that request succeeds, so nobody can
 * "agree" to text the page failed to load.
 */
export function RegisterForm() {
  const [catalogue, setCatalogue] = useState<ConsentCatalogue | null>(null);
  const [catalogueError, setCatalogueError] = useState<string | null>(null);
  const [reloadToken, setReloadToken] = useState(0);
  const [selected, setSelected] = useState<readonly string[]>([]);
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [created, setCreated] = useState<SessionResponse | null>(null);

  useEffect(() => {
    let cancelled = false;
    fetchConsentCatalogue()
      .then((data) => {
        if (!cancelled) {
          setCatalogue(data);
        }
      })
      .catch((failure: unknown) => {
        if (!cancelled) {
          setCatalogueError(describeApiError(failure));
        }
      });
    return () => {
      cancelled = true;
    };
  }, [reloadToken]);

  function toggleConsent(kind: string, checked: boolean) {
    setSelected((current) =>
      checked
        ? [...current.filter((item) => item !== kind), kind]
        : current.filter((item) => item !== kind),
    );
  }

  const requiredKinds =
    catalogue?.consents
      .filter((item) => item.required_for_registration)
      .map((item) => item.kind) ?? [];
  const missingRequired = requiredKinds.filter((kind) => !selected.includes(kind));
  const canSubmit = catalogue !== null && missingRequired.length === 0 && !submitting;

  async function onSubmit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setError(null);
    setSubmitting(true);
    try {
      setCreated(await registerAccount({ email, password, consents: selected }));
      // The password is not needed after a successful registration.
      setPassword("");
    } catch (failure) {
      setError(describeApiError(failure));
    } finally {
      setSubmitting(false);
    }
  }

  if (created) {
    return (
      <div className="panel" role="status">
        <h2>Account created</h2>
        <p>
          You are signed in as <strong>{created.account.email}</strong>. Your session expires at{" "}
          <time dateTime={created.expires_at}>{created.expires_at}</time>.
        </p>
        <p className="hint">
          Next: fill in your profile, then upload a CV or transcript. Reviewing consent is
          optional until a screen needs a consent you have not granted.
        </p>
        <div className="button-row">
          <a className="button" href="/onboarding">
            Set up your profile
          </a>
          <a className="button button-quiet" href="/consent">
            Review consent choices
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
        <label htmlFor="register-email">Email address</label>
        <input
          id="register-email"
          name="email"
          type="email"
          autoComplete="email"
          required
          value={email}
          onChange={(event) => setEmail(event.target.value)}
        />
      </div>

      <div className="field">
        <label htmlFor="register-password">Password</label>
        <input
          id="register-password"
          name="password"
          type="password"
          autoComplete="new-password"
          required
          minLength={10}
          aria-describedby="register-password-hint"
          value={password}
          onChange={(event) => setPassword(event.target.value)}
        />
        <p className="hint" id="register-password-hint">
          At least 10 characters. Length matters more than symbols; the server also refuses the
          most common passwords and your email name.
        </p>
      </div>

      <fieldset className="consent-fieldset">
        <legend>Consent</legend>
        <p className="hint">
          The server records exactly these texts against a policy version. Tick only what you agree
          to; both required consents must be granted before an account can be created.
        </p>
        {catalogueError ? (
          <>
            <p className="alert" role="alert">
              {catalogueError}
            </p>
            <button
              type="button"
              className="button button-quiet"
              onClick={() => {
                setCatalogue(null);
                setCatalogueError(null);
                setReloadToken((token) => token + 1);
              }}
            >
              Try loading the consent texts again
            </button>
          </>
        ) : catalogue === null ? (
          <p className="hint" aria-live="polite">
            Loading the consent texts…
          </p>
        ) : (
          <ul className="consent-options" role="list">
            {catalogue.consents.map((item) => (
              <li className="check" key={item.kind}>
                <input
                  id={`consent-${item.kind}`}
                  name={`consent-${item.kind}`}
                  type="checkbox"
                  checked={selected.includes(item.kind)}
                  onChange={(event) => toggleConsent(item.kind, event.target.checked)}
                />
                <label htmlFor={`consent-${item.kind}`}>
                  {item.text}
                  <span className="consent-kind">
                    {item.required_for_registration ? "Required for registration" : "Optional"}
                  </span>
                </label>
              </li>
            ))}
          </ul>
        )}
      </fieldset>

      <div className="button-row">
        <button className="button" type="submit" disabled={!canSubmit}>
          {submitting ? "Creating account…" : "Create account"}
        </button>
        <a href="/login">I already have an account</a>
      </div>
      {catalogue !== null && missingRequired.length > 0 ? (
        <p className="hint">
          Tick every consent marked as required to enable the button.
        </p>
      ) : null}
    </form>
  );
}
