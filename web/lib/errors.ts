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
      return "A required consent is missing. Grant it on the consent page, then try again.";
    case "unknown_consent":
      return "This page asked for a consent the server does not know. Reload the page and try again.";
    case "invalid_session":
      return "Your session has expired. Sign in again.";
    case "profile_missing":
      return "No profile is saved yet. Fill in your profile first, then try again.";
    case "invalid_profile":
      return error.message;
    case "document_too_large":
      // No number here on purpose: the limit lives in `documents.MAX_DOCUMENT_BYTES`,
      // and a hardcoded size in this file would drift from it.
      return "That file is larger than the upload limit. Compress it, or upload a smaller version.";
    case "unsupported_document":
      return "Upload a PDF, DOCX or TXT file. Other formats are not accepted.";
    case "document_unreadable":
      return "The text could not be extracted from that document. A scanned or password-protected PDF needs OCR, which is not available yet.";
    case "document_missing":
      return "That document is no longer available. Reload the list and try again.";
    case "fact_missing":
      return "That fact is no longer available. Reload the list and try again.";
    case "deletion_incomplete":
      return "The document was not deleted because its stored file is in use. Close any program using it and try again.";
    case "shortlist_full":
      return "Your shortlist is full. Remove some entries before adding more.";
    case "shortlist_item_missing":
      return "That entry is no longer on your shortlist. Reload the page.";
    case "faculty_missing":
    case "program_missing":
      return "That record is no longer in the research cache. Reload the page.";
    case "draft_changed":
      return "This draft changed since you reviewed it. The latest version is shown; review it before approving.";
    case "draft_recipient_unverified":
      return "This professor's email is no longer verified. Regenerate the draft once it is verified again.";
    case "draft_lint_blocked":
      return "Fix the blocking issues listed next to the draft before approving.";
    case "draft_wrong_cv":
      return "That CV version was approved for someone else. Attach your original or the version approved for this professor.";
    case "draft_missing":
      return "That draft is no longer available. Reload the page.";
    case "invalid_draft":
      return error.message;
    case "cv_nothing_accepted":
      return "Accept or edit at least one suggestion before approving.";
    case "cv_nothing_changed":
      return "The accepted suggestions do not change your CV. Edit a flagged line, or accept a reorder or a skill, then approve.";
    case "cv_suggestion_missing":
      return "These suggestions were refreshed because your profile or the record changed. The latest ones are shown below.";
    case "cv_version_missing":
    case "cv_run_missing":
      return "That CV version or analysis is no longer available. Reload the page.";
    case "invalid_cv":
      return error.message;
    case "invalid_matching":
      return "Matches could not be computed right now. The operator has been notified; try again later.";
    case "invalid_input":
      return error.message;
    case "network_error":
      return error.message;
    default:
      return error.status === 422
        ? "Check the highlighted fields and try again."
        : error.message;
  }
}
