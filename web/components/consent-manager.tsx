"use client";

// Plain anchors are deliberate: see the note in register-form.tsx.
/* eslint-disable @next/next/no-html-link-for-pages */

import { useEffect, useState } from "react";

import {
  ApiError,
  fetchConsents,
  grantConsent,
  signOut,
  type ConsentList,
} from "@/lib/api";
import { describeApiError } from "@/lib/errors";

type LoadState = "loading" | "ready" | "signed-out" | "failed";

/** `personal_data` → `Personal data`; the API carries ids, not labels. */
function humaniseKind(kind: string): string {
  const words = kind.replaceAll("_", " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/**
 * The signed-in consent screen.
 *
 * It reads the state from the API rather than trusting local storage: consent is
 * a ledger row, not a checkbox on a client. Granting is idempotent on the server,
 * so the button can be retried safely after a network failure.
 */
export function ConsentManager() {
  const [state, setState] = useState<LoadState>("loading");
  const [data, setData] = useState<ConsentList | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busyKind, setBusyKind] = useState<string | null>(null);
  const [signingOut, setSigningOut] = useState(false);
  const [reloadToken, setReloadToken] = useState(0);

  useEffect(() => {
    let cancelled = false;
    fetchConsents()
      .then((list) => {
        if (cancelled) {
          return;
        }
        setData(list);
        setState("ready");
      })
      .catch((failure: unknown) => {
        if (cancelled) {
          return;
        }
        if (failure instanceof ApiError && failure.status === 401) {
          setState("signed-out");
          return;
        }
        setError(describeApiError(failure));
        setState("failed");
      });
    return () => {
      cancelled = true;
    };
  }, [reloadToken]);

  async function onGrant(kind: string) {
    setBusyKind(kind);
    setError(null);
    setNotice(null);
    try {
      const accepted = await grantConsent(kind);
      setNotice(
        accepted.already_granted
          ? "That consent was already recorded for the current policy."
          : "Consent recorded. The server logged it against the current policy version.",
      );
      setData(await fetchConsents());
    } catch (failure) {
      setError(describeApiError(failure));
    } finally {
      setBusyKind(null);
    }
  }

  async function onSignOut() {
    setSigningOut(true);
    setError(null);
    try {
      await signOut();
      setData(null);
      setState("signed-out");
    } catch (failure) {
      setError(describeApiError(failure));
    } finally {
      setSigningOut(false);
    }
  }

  if (state === "loading") {
    return (
      <p className="hint" aria-live="polite">
        Checking your session…
      </p>
    );
  }

  if (state === "signed-out") {
    return (
      <div className="panel" role="status">
        <h2>You are not signed in</h2>
        <p>
          Consent belongs to a signed-in account. Sign in, or create an account first.
        </p>
        <div className="button-row">
          <a className="button" href="/login">
            Sign in
          </a>
          <a className="button button-quiet" href="/register">
            Create an account
          </a>
        </div>
      </div>
    );
  }

  if (state === "failed" || data === null) {
    return (
      <div className="panel">
        <p className="alert" role="alert">
          {error ?? "The consent state could not be loaded."}
        </p>
        <button
          type="button"
          className="button button-quiet"
          onClick={() => {
            setData(null);
            setError(null);
            setState("loading");
            setReloadToken((token) => token + 1);
          }}
        >
          Try again
        </button>
      </div>
    );
  }

  return (
    <div className="panel">
      {error ? (
        <p className="alert" role="alert">
          {error}
        </p>
      ) : null}
      {notice ? (
        <p className="alert alert-ok" role="status">
          {notice}
        </p>
      ) : null}

      <p className="hint">
        Policy version <strong>{data.policy_version}</strong>. Changing the policy text asks for
        consent again instead of assuming an old answer still applies.
      </p>

      <ul className="status-list" role="list">
        {data.consents.map((item) => (
          <li key={item.kind}>
            <div>
              <h3>{humaniseKind(item.kind)}</h3>
              <p>{item.text}</p>
            </div>
            <div>
              {item.granted ? (
                <span className="granted">Granted</span>
              ) : (
                <button
                  type="button"
                  className="button button-quiet"
                  disabled={busyKind !== null}
                  onClick={() => onGrant(item.kind)}
                >
                  {busyKind === item.kind ? "Recording…" : "Grant this consent"}
                </button>
              )}
            </div>
          </li>
        ))}
      </ul>

      <div className="button-row">
        <button
          type="button"
          className="button button-quiet"
          onClick={onSignOut}
          disabled={signingOut}
        >
          {signingOut ? "Signing out…" : "Sign out"}
        </button>
        <a href="/">Back to the start page</a>
      </div>
    </div>
  );
}
