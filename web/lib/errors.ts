/**
 * Map an API failure to one sentence a student can act on.
 *
 * The API's `message` is already actionable and never echoes a password, so it
 * is used for input validation. Codes that need product wording (a duplicate
 * email, a lockout) get their own sentence here, which keeps the copy in one
 * tested place instead of inside each form.
 */

import { ApiError } from "./api";

/** "45 seconds", "1 minute", "15 minutes" — no locale formatting. */
export function formatWait(seconds: number): string {
  const whole = Math.max(1, Math.ceil(seconds));
  if (whole < 60) {
    return `${whole} second${whole === 1 ? "" : "s"}`;
  }
  const minutes = Math.ceil(whole / 60);
  return `${minutes} minute${minutes === 1 ? "" : "s"}`;
}

export function describeApiError(error: unknown): string {
  if (!(error instanceof ApiError)) {
    return "Something went wrong. Please try again.";
  }

  switch (error.code) {
    case "email_taken":
      return "That email already has an account. Sign in instead.";
    case "invalid_credentials":
      return "Email or password is incorrect.";
    case "account_locked":
    case "rate_limited":
      return error.retryAfterSeconds
        ? `Too many attempts. Try again in ${formatWait(error.retryAfterSeconds)}.`
        : "Too many attempts. Try again later.";
    case "consent_required":
      return "Tick every consent marked as required, then try again.";
    case "unknown_consent":
      return "This page asked for a consent the server does not know. Reload the page and try again.";
    case "invalid_session":
      return "Your session has expired. Sign in again.";
    case "network_error":
      return error.message;
    case "invalid_input":
      return error.message;
    default:
      return error.status === 422
        ? "Check the highlighted fields and try again."
        : error.message;
  }
}
