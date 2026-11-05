import { beforeEach, describe, expect, it, vi } from "vitest";

import {
  API_BASE_URL,
  ApiError,
  applyFacts,
  deleteDocument,
  fetchConsentCatalogue,
  fetchProfile,
  grantConsent,
  listDocuments,
  listFacts,
  loginAccount,
  parseDocument,
  parseRetryAfter,
  registerAccount,
  resolveApiBaseUrl,
  saveProfile,
  signOut,
  updateFact,
  uploadDocument,
  type ProfileInput,
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

const PROFILE_INPUT: ProfileInput = {
  full_name: "Ada Lovelace",
  contact_email: "ada@example.edu",
  preferred_name: null,
  phone: null,
  institution: "Example University",
  department: null,
  country: null,
  degree_type: "bs",
  current_year: 3,
  expected_graduation: "2027-06-30",
  gpa: 3.8,
  gpa_scale: 4,
  coursework: ["Algorithms"],
  skills: ["Python"],
  research_keywords: ["graph theory"],
  target_countries: ["Germany"],
  internship_start: null,
  internship_end: null,
  funding_required: true,
};

describe("profile client", () => {
  it("reads the profile without a body", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ ...PROFILE_INPUT, user_id: 1, completeness_percent: 80 }),
    );

    await fetchProfile();

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(`${API_BASE_URL}/api/profile`);
    expect(init.method).toBe("GET");
    expect(init.body).toBeUndefined();
    expect(init.credentials).toBe("include");
  });

  it("replaces the profile with PUT and keeps every field", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ ...PROFILE_INPUT, user_id: 1, completeness_percent: 80 }),
    );

    await saveProfile(PROFILE_INPUT);

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(`${API_BASE_URL}/api/profile`);
    expect(init.method).toBe("PUT");
    expect(JSON.parse(String(init.body))).toEqual(PROFILE_INPUT);
  });

  it("keeps profile_missing distinguishable from a transport failure", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(
        { error: { code: "profile_missing", message: "profile not found." } },
        { status: 404 },
      ),
    );

    await expect(fetchProfile()).rejects.toMatchObject({
      status: 404,
      code: "profile_missing",
    });
  });
});

describe("document client", () => {
  it("lists documents", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ documents: [] }));

    await listDocuments();

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(`${API_BASE_URL}/api/documents`);
    expect(init.method).toBe("GET");
  });

  it("uploads multipart form data without a JSON content type", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ id: 1, display_name: "cv.pdf" }, { status: 201 }));
    const file = new File(["%PDF-1.4"], "cv.pdf", { type: "application/pdf" });

    await uploadDocument(file, "cv");

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(`${API_BASE_URL}/api/documents`);
    expect(init.method).toBe("POST");
    expect(init.body).toBeInstanceOf(FormData);
    const form = init.body as FormData;
    expect(form.get("kind")).toBe("cv");
    const uploaded = form.get("file");
    expect(uploaded).toBeInstanceOf(File);
    expect((uploaded as File).name).toBe("cv.pdf");
    // The browser must set the multipart boundary itself.
    expect((init.headers as Record<string, string>)["Content-Type"]).toBeUndefined();
  });

  it("parses and deletes by id", async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse({ document: { id: 4 }, character_count: 12, fact_count: 2 }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));

    await parseDocument(4);
    await expect(deleteDocument(4)).resolves.toBeUndefined();

    const [parseUrl, parseInit] = fetchMock.mock.calls[0] as [string, RequestInit];
    const [deleteUrl, deleteInit] = fetchMock.mock.calls[1] as [string, RequestInit];
    expect(parseUrl).toBe(`${API_BASE_URL}/api/documents/4/parse`);
    expect(parseInit.method).toBe("POST");
    expect(parseInit.body).toBeUndefined();
    expect(deleteUrl).toBe(`${API_BASE_URL}/api/documents/4`);
    expect(deleteInit.method).toBe("DELETE");
  });

  it("passes an upload error code through for the page copy", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse(
        { error: { code: "document_too_large", message: "too large." } },
        { status: 413 },
      ),
    );

    await expect(
      uploadDocument(new File(["x"], "big.pdf"), "cv"),
    ).rejects.toMatchObject({ status: 413, code: "document_too_large" });
  });
});

describe("fact client", () => {
  it("reads groups", async () => {
    fetchMock.mockResolvedValue(jsonResponse({ groups: [] }));

    await listFacts();

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(`${API_BASE_URL}/api/facts`);
    expect(init.method).toBe("GET");
  });

  it("patches a confirmation with an optional correction", async () => {
    fetchMock.mockResolvedValue(
      jsonResponse({ fact_id: 3, status: "confirmed", already_set: false }),
    );

    await updateFact(3, { status: "confirmed", correctedValue: "  3.9  " });

    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url).toBe(`${API_BASE_URL}/api/facts/3`);
    expect(init.method).toBe("PATCH");
    expect(JSON.parse(String(init.body))).toEqual({
      status: "confirmed",
      corrected_value: "3.9",
    });
  });

  it("omits corrected_value when rejecting or when the field is blank", async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse({ fact_id: 3, status: "rejected", already_set: false }))
      .mockResolvedValueOnce(jsonResponse({ fact_id: 4, status: "confirmed", already_set: false }));

    await updateFact(3, { status: "rejected", correctedValue: "ignored" });
    await updateFact(4, { status: "confirmed", correctedValue: "   " });

    const [, rejectedInit] = fetchMock.mock.calls[0] as [string, RequestInit];
    const [, blankInit] = fetchMock.mock.calls[1] as [string, RequestInit];
    expect(JSON.parse(String(rejectedInit.body))).toEqual({ status: "rejected" });
    expect(JSON.parse(String(blankInit.body))).toEqual({ status: "confirmed" });
  });

  it("applies with explicit defaults and with overwrite", async () => {
    fetchMock
      .mockResolvedValueOnce(jsonResponse({ applied: ["gpa"] }))
      .mockResolvedValueOnce(jsonResponse({ applied: [] }));

    await applyFacts();
    await applyFacts({ fields: ["gpa"], overwrite: true });

    const [firstUrl, firstInit] = fetchMock.mock.calls[0] as [string, RequestInit];
    const [, secondInit] = fetchMock.mock.calls[1] as [string, RequestInit];
    expect(JSON.parse(String(firstInit.body))).toEqual({ fields: null, overwrite: false });
    expect(JSON.parse(String(secondInit.body))).toEqual({ fields: ["gpa"], overwrite: true });
    expect(firstInit.method).toBe("POST");
    expect(firstUrl).toBe(`${API_BASE_URL}/api/facts/apply`);
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
