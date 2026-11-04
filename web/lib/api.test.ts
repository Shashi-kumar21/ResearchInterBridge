import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  API_BASE_URL,
  ApiError,
  fetchConsentCatalogue,
  grantConsent,
  loginAccount,
  parseRetryAfter,
  registerAccount,
  resolveApiBaseUrl,
  signOut,
} from "./api";

const fetchMock = vi.fn();

function jsonResponse(body: unknown, init: ResponseInit = {}): Response {
  return new Response(JSON.stringify(body), {
    status: init.status ?? 200,
    headers: { "content-type": "application/json", ...init.headers },
  });
}

const SESSION = {
  account: { user_id: 1, email: "student@example.edu", created_at: "2026-11-01T00:00:00Z" },
  expires_at: "2026-11-04T00:00:00Z",
};

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

describe("resolveApiBaseUrl", () => {
  it("falls back to the local API when unset or blank", () => {
    expect(resolveApiBaseUrl(undefined)).toBe("http://localhost:8000");
    expect(resolveApiBaseUrl("   ")).toBe("http://localhost:8000");
  });

  it("trims whitespace and trailing slashes", () => {
    expect(resolveApiBaseUrl("  https://api.example.edu/  ")).toBe("https://api.example.edu");
    expect(resolveApiBaseUrl("http://localhost:8000///")).toBe("http://localhost:8000");
  });
});

describe("parseRetryAfter", () => {
  it("reads whole seconds and rounds up", () => {
    expect(parseRetryAfter("120")).toBe(120);
    expect(parseRetryAfter("0.4")).toBe(1);
  });

  it("ignores missing, zero, negative and HTTP-date values", () => {
    expect(parseRetryAfter(null)).toBeNull();
    expect(parseRetryAfter("0")).toBeNull();
    expect(parseRetryAfter("-5")).toBeNull();
    expect(parseRetryAfter("Wed, 21 Oct 2026 07:28:00 GMT")).toBeNull();
  });
});

describe("registerAccount", () => {
  it("posts the consents as JSON with the session cookie attached", async () => {
    fetchMock.mockResolvedValue(jsonResponse(SESSION, { status: 201 }));

    const session = await registerAccount({
      email: "student@example.edu",
      password: "correct horse battery staple",
      consents: ["personal_data", "document_processing"],
    });

    expect(session.account.email).toBe("student@example.edu");
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(`${API_BASE_URL}/api/auth/register`);
    expect(init.credentials).toBe("include");
    expect(init.method).toBe("POST");
    expect(JSON.parse(String(init.body))).toEqual({
      email: "student@example.edu",
      password: "correct horse battery staple",
      consents: ["personal_data", "document_processing"],
    });
  });

  it("turns the API error envelope into a coded ApiError", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(
        { error: { code: "email_taken", message: "an account with this email already exists." } },
        { status: 409 },
      ),
    );

    await expect(
      registerAccount({ email: "student@example.edu", password: "x", consents: [] }),
    ).rejects.toMatchObject({
      status: 409,
      code: "email_taken",
      message: "an account with this email already exists.",
    });
  });
});

describe("loginAccount", () => {
  it("keeps Retry-After on a 429 so the form can say how long to wait", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(
        { error: { code: "account_locked", message: "too many failed attempts." } },
        { status: 429, headers: { "retry-after": "900" } },
      ),
    );

    const failure = await loginAccount({ email: "a@b.edu", password: "x" }).catch(
      (error: unknown) => error,
    );

    expect(failure).toBeInstanceOf(ApiError);
    expect(failure).toMatchObject({ status: 429, code: "account_locked", retryAfterSeconds: 900 });
  });
});

describe("responses that are not the API envelope", () => {
  it("reports a non-JSON failure by status", async () => {
    fetchMock.mockResolvedValue(new Response("<html>bad gateway</html>", { status: 502 }));

    const failure = await fetchConsentCatalogue().catch((error: unknown) => error);

    expect(failure).toMatchObject({ status: 502, code: "http_error" });
    expect((failure as ApiError).message).toContain("502");
  });

  it("rejects a success body that is not JSON instead of returning undefined", async () => {
    fetchMock.mockResolvedValue(new Response("not json", { status: 200 }));

    await expect(fetchConsentCatalogue()).rejects.toMatchObject({ code: "invalid_response" });
  });

  it("treats a 204 as success with no body", async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 204 }));

    await expect(signOut()).resolves.toBeUndefined();
    const [, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(init.method).toBe("POST");
  });

  it("reports a request that never reached the server", async () => {
    fetchMock.mockRejectedValue(new TypeError("fetch failed"));

    const failure = await grantConsent("gmail_access").catch((error: unknown) => error);

    expect(failure).toBeInstanceOf(ApiError);
    expect(failure).toMatchObject({ status: 0, code: "network_error" });
    expect(String((failure as ApiError).message)).toMatch(/backend is running/i);
  });
});

describe("request shape", () => {
  it("reads the public catalogue without a body", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ policy_version: "2026-10-31", consents: [] }),
    );

    await fetchConsentCatalogue();

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(`${API_BASE_URL}/api/consents/catalogue`);
    expect(init.body).toBeUndefined();
    expect(init.credentials).toBe("include");
  });

  it("posts one consent kind by name", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({
        policy_version: "2026-10-31",
        kind: "gmail_access",
        already_granted: false,
      }),
    );

    await grantConsent("gmail_access");

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(`${API_BASE_URL}/api/consents`);
    expect(JSON.parse(String(init.body))).toEqual({ kind: "gmail_access" });
  });
});
