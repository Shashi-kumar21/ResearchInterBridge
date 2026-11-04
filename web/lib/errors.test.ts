import { describe, expect, it } from "vitest";

import { ApiError } from "./api";
import { describeApiError, formatWait } from "./errors";

function apiError(code: string, status = 400, retryAfterSeconds?: number): ApiError {
  return new ApiError("server message", { code, status, retryAfterSeconds });
}

describe("formatWait", () => {
  it("uses seconds below a minute and minutes above", () => {
    expect(formatWait(1)).toBe("1 second");
    expect(formatWait(59)).toBe("59 seconds");
    expect(formatWait(60)).toBe("1 minute");
    expect(formatWait(61)).toBe("2 minutes");
    expect(formatWait(900)).toBe("15 minutes");
  });
});

describe("describeApiError", () => {
  it("points a duplicate email at sign-in", () => {
    expect(describeApiError(apiError("email_taken", 409))).toMatch(/sign in/i);
  });

  it("gives one answer for a wrong password and an unknown email", () => {
    expect(describeApiError(apiError("invalid_credentials", 401))).toBe(
      "Email or password is incorrect.",
    );
  });

  it("quotes the Retry-After for a lockout or rate limit", () => {
    expect(describeApiError(apiError("account_locked", 429, 900))).toBe(
      "Too many attempts. Try again in 15 minutes.",
    );
    expect(describeApiError(apiError("rate_limited", 429, 30))).toBe(
      "Too many attempts. Try again in 30 seconds.",
    );
  });

  it("does not invent a wait when the header is absent", () => {
    expect(describeApiError(apiError("rate_limited", 429))).toBe(
      "Too many attempts. Try again later.",
    );
  });

  it("shows the server message for invalid input, which never echoes the password", () => {
    expect(describeApiError(apiError("invalid_input", 400))).toBe("server message");
  });

  it("asks for consent when the server requires it", () => {
    expect(describeApiError(apiError("consent_required", 403))).toMatch(/required/);
  });

  it("handles known session and transport failures", () => {
    expect(describeApiError(apiError("invalid_session", 401))).toMatch(/sign in again/i);
    expect(describeApiError(apiError("network_error", 0))).toBe("server message");
  });

  it("keeps a 422 generic and a non-API failure safe", () => {
    expect(describeApiError(apiError("invalid_request", 422))).toMatch(/highlighted fields/);
    expect(describeApiError(new Error("boom"))).toBe("Something went wrong. Please try again.");
  });

  it("explains the content API codes in student terms", () => {
    expect(describeApiError(apiError("profile_missing", 404))).toMatch(/profile/i);
    expect(describeApiError(apiError("document_too_large", 413))).toMatch(/10 MB/);
    expect(describeApiError(apiError("unsupported_document", 415))).toMatch(/PDF, DOCX or TXT/);
    expect(describeApiError(apiError("document_unreadable", 422))).toMatch(/OCR/);
    expect(describeApiError(apiError("deletion_incomplete", 409))).toMatch(/in use/);
  });

  it("uses the server's field message for an invalid profile", () => {
    expect(describeApiError(apiError("invalid_profile", 400))).toBe("server message");
  });
});
