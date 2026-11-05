"use client";

// Plain anchors are deliberate inside client components: see the note in
// register-form.tsx.

/**
 * Shared states for the signed-in content screens.
 *
 * They look the same on purpose: a student who is signed out, or whose API call
 * failed, should get one predictable panel instead of a different sentence on
 * every page.
 */

export function SignedOutPanel({ intro }: { readonly intro: string }) {
  return (
    <div className="panel" role="status">
      <h2>You are not signed in</h2>
      <p>{intro}</p>
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

export function RetryPanel({
  message,
  onRetry,
}: {
  readonly message: string;
  readonly onRetry: () => void;
}) {
  return (
    <div className="panel">
      <p className="alert" role="alert">
        {message}
      </p>
      <button type="button" className="button button-quiet" onClick={onRetry}>
        Try again
      </button>
    </div>
  );
}

export function LoadingPanel({ message }: { readonly message: string }) {
  return (
    <p className="hint" aria-live="polite">
      {message}
    </p>
  );
}
