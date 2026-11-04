/**
 * Browser client for the Python JSON API (ADR-012).
 *
 * The API is a separate origin in development (`localhost:8000`), so every call
 * uses `credentials: "include"`: the session cookie is HttpOnly and host-scoped
 * to the API, and the browser is the only thing allowed to attach it. No token
 * ever passes through JavaScript, which is the point of the cookie design.
 *
 * Errors are normalised into :class:`ApiError` so a form can branch on the stable
 * `code` from the API instead of matching message text. The message is shown to
 * the student; it never contains a password or a session token.
 */

export const DEFAULT_API_BASE_URL = "http://localhost:8000";

/**
 * Normalise `NEXT_PUBLIC_API_BASE_URL`: trim, drop trailing slashes, and fall
 * back to the local development API. Kept pure so the rule is unit-tested
 * without reading the environment.
 */
export function resolveApiBaseUrl(value: string | undefined): string {
  const trimmed = (value ?? "").trim();
  if (!trimmed) {
    return DEFAULT_API_BASE_URL;
  }
  return trimmed.replace(/\/+$/, "");
}

/** Resolved at build time: Next inlines `NEXT_PUBLIC_*` for client bundles. */
export const API_BASE_URL = resolveApiBaseUrl(process.env.NEXT_PUBLIC_API_BASE_URL);

export interface AccountSummary {
  readonly user_id: number;
  readonly email: string;
  readonly created_at: string;
}

export interface SessionResponse {
  readonly account: AccountSummary;
  readonly expires_at: string;
}

export interface ConsentCatalogueItem {
  readonly kind: string;
  readonly text: string;
  readonly required_for_registration: boolean;
}

export interface ConsentCatalogue {
  readonly policy_version: string;
  readonly consents: readonly ConsentCatalogueItem[];
}

export interface ConsentState {
  readonly kind: string;
  readonly text: string;
  readonly granted: boolean;
}

export interface ConsentList {
  readonly policy_version: string;
  readonly consents: readonly ConsentState[];
}

export interface ConsentAccepted {
  readonly policy_version: string;
  readonly kind: string;
  readonly already_granted: boolean;
}

export interface ApiErrorOptions {
  readonly status: number;
  readonly code: string;
  readonly retryAfterSeconds?: number | null;
}

export class ApiError extends Error {
  /** HTTP status, or 0 when the request never reached the server. */
  readonly status: number;
  /** Stable code from the API envelope (`rate_limited`, `email_taken`, ...). */
  readonly code: string;
  /** Whole seconds from `Retry-After`, when the API sent one. */
  readonly retryAfterSeconds: number | null;

  constructor(message: string, options: ApiErrorOptions) {
    super(message);
    this.name = "ApiError";
    this.status = options.status;
    this.code = options.code;
    this.retryAfterSeconds = options.retryAfterSeconds ?? null;
  }
}

interface ErrorEnvelope {
  readonly error?: {
    readonly code?: unknown;
    readonly message?: unknown;
  };
}

/** Parse a whole-seconds `Retry-After`, ignoring HTTP-date and junk values. */
export function parseRetryAfter(value: string | null): number | null {
  if (!value) {
    return null;
  }
  const seconds = Number(value.trim());
  if (!Number.isFinite(seconds) || seconds <= 0) {
    return null;
  }
  return Math.ceil(seconds);
}

async function readError(response: Response): Promise<ApiError> {
  let code = "http_error";
  let message = `The request failed with status ${response.status}.`;
  try {
    const body = (await response.json()) as ErrorEnvelope;
    if (typeof body.error?.code === "string") {
      code = body.error.code;
    }
    if (typeof body.error?.message === "string" && body.error.message.trim()) {
      message = body.error.message;
    }
  } catch {
    // A proxy or crash can return HTML; the status is still actionable.
  }
  return new ApiError(message, {
    status: response.status,
    code,
    retryAfterSeconds: parseRetryAfter(response.headers.get("retry-after")),
  });
}

interface RequestOptions {
  readonly method?: "GET" | "POST" | "PUT" | "PATCH" | "DELETE";
  readonly body?: unknown;
}

async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const headers: Record<string, string> = { Accept: "application/json" };
  // `FormData` must set its own multipart boundary. Setting a JSON type here
  // would make Starlette parse the upload as JSON and reject the request.
  const isMultipart = typeof FormData !== "undefined" && options.body instanceof FormData;
  if (options.body !== undefined && !isMultipart) {
    headers["Content-Type"] = "application/json";
  }

  let response: Response;
  try {
    response = await fetch(`${API_BASE_URL}${path}`, {
      method: options.method ?? "GET",
      headers,
      credentials: "include",
      cache: "no-store",
      body:
        options.body === undefined
          ? undefined
          : isMultipart
            ? (options.body as FormData)
            : JSON.stringify(options.body),
    });
  } catch {
    throw new ApiError(
      "Could not reach the API. Check that the backend is running and that the site is allowed by RIB_CORS_ORIGINS.",
      { status: 0, code: "network_error" },
    );
  }

  if (!response.ok) {
    throw await readError(response);
  }
  if (response.status === 204) {
    return undefined as T;
  }
  try {
    return (await response.json()) as T;
  } catch {
    throw new ApiError("The API returned a response this page could not read.", {
      status: response.status,
      code: "invalid_response",
    });
  }
}

export function fetchConsentCatalogue(): Promise<ConsentCatalogue> {
  return request<ConsentCatalogue>("/api/consents/catalogue");
}

export interface RegisterInput {
  readonly email: string;
  readonly password: string;
  readonly consents: readonly string[];
}

export function registerAccount(input: RegisterInput): Promise<SessionResponse> {
  return request<SessionResponse>("/api/auth/register", {
    method: "POST",
    body: {
      email: input.email,
      password: input.password,
      consents: [...input.consents],
    },
  });
}

export interface LoginInput {
  readonly email: string;
  readonly password: string;
}

export function loginAccount(input: LoginInput): Promise<SessionResponse> {
  return request<SessionResponse>("/api/auth/login", {
    method: "POST",
    body: { email: input.email, password: input.password },
  });
}

export function signOut(): Promise<void> {
  return request<void>("/api/auth/logout", { method: "POST" });
}

export function fetchConsents(): Promise<ConsentList> {
  return request<ConsentList>("/api/consents");
}

export function grantConsent(kind: string): Promise<ConsentAccepted> {
  return request<ConsentAccepted>("/api/consents", {
    method: "POST",
    body: { kind },
  });
}

// --------------------------------------------------------------------------- //
// profile
// --------------------------------------------------------------------------- //

/** Degree values accepted by `profiles.DegreeType`. */
export type DegreeType =
  | "btech"
  | "be"
  | "bs"
  | "msc"
  | "mtech"
  | "dual_degree"
  | "phd"
  | "other";

/**
 * The body of `PUT /api/profile`.
 *
 * `PUT` is a full replacement, so every editable field is required here: a
 * caller must decide explicitly whether a value is cleared or kept. The server
 * derives `user_id`, `consent_given_at` and the timestamps, so they are absent.
 */
export interface ProfileInput {
  readonly full_name: string;
  readonly contact_email: string;
  readonly preferred_name: string | null;
  readonly phone: string | null;
  readonly institution: string | null;
  readonly department: string | null;
  readonly country: string | null;
  readonly degree_type: DegreeType | null;
  readonly current_year: number | null;
  readonly expected_graduation: string | null;
  readonly gpa: number | null;
  readonly gpa_scale: number | null;
  readonly coursework: readonly string[];
  readonly skills: readonly string[];
  readonly research_keywords: readonly string[];
  readonly target_countries: readonly string[];
  readonly internship_start: string | null;
  readonly internship_end: string | null;
  readonly funding_required: boolean | null;
}

export interface StudentProfile extends ProfileInput {
  readonly user_id: number;
  readonly consent_given_at: string | null;
  readonly created_at: string | null;
  readonly updated_at: string | null;
  readonly completeness_percent: number;
  readonly completeness_missing: readonly string[];
}

export function fetchProfile(): Promise<StudentProfile> {
  return request<StudentProfile>("/api/profile");
}

export function saveProfile(input: ProfileInput): Promise<StudentProfile> {
  return request<StudentProfile>("/api/profile", { method: "PUT", body: input });
}

// --------------------------------------------------------------------------- //
// documents
// --------------------------------------------------------------------------- //

export type DocumentKind = "cv" | "transcript" | "other";

export interface DocumentRecord {
  readonly id: number;
  readonly kind: string;
  readonly display_name: string;
  readonly media_type: string;
  readonly size_bytes: number;
  readonly status: string;
  readonly sha256: string;
  readonly uploaded_at: string;
  readonly parsed_at: string | null;
  readonly parse_error: string | null;
}

export interface DocumentList {
  readonly documents: readonly DocumentRecord[];
}

export interface ParseResult {
  readonly document: DocumentRecord;
  readonly character_count: number;
  readonly fact_count: number;
}

export function listDocuments(): Promise<DocumentList> {
  return request<DocumentList>("/api/documents");
}

export function uploadDocument(file: File, kind: DocumentKind): Promise<DocumentRecord> {
  const form = new FormData();
  form.append("file", file, file.name);
  form.append("kind", kind);
  return request<DocumentRecord>("/api/documents", { method: "POST", body: form });
}

export function parseDocument(documentId: number): Promise<ParseResult> {
  return request<ParseResult>(`/api/documents/${documentId}/parse`, { method: "POST" });
}

export function deleteDocument(documentId: number): Promise<void> {
  return request<void>(`/api/documents/${documentId}`, { method: "DELETE" });
}

// --------------------------------------------------------------------------- //
// facts
// --------------------------------------------------------------------------- //

export interface FactCandidate {
  readonly id: number;
  readonly field: string;
  readonly value: string;
  readonly source: string;
  readonly status: string;
  readonly confidence: number | null;
}

export interface FactGroup {
  readonly field: string;
  readonly pending: readonly FactCandidate[];
  readonly confirmed: readonly FactCandidate[];
  readonly has_conflict: boolean;
}

export interface FactList {
  readonly groups: readonly FactGroup[];
}

export interface FactUpdateInput {
  readonly status: "confirmed" | "rejected";
  /** Replaces the extracted text when confirming; ignored when rejecting. */
  readonly correctedValue?: string | null;
}

export interface FactUpdateResult {
  readonly fact_id: number;
  readonly status: string;
  readonly already_set: boolean;
}

export interface FactApplyInput {
  readonly fields?: readonly string[] | null;
  readonly overwrite?: boolean;
}

export interface FactApplyResult {
  readonly applied: readonly string[];
}

export function listFacts(): Promise<FactList> {
  return request<FactList>("/api/facts");
}

export function updateFact(factId: number, input: FactUpdateInput): Promise<FactUpdateResult> {
  const body: { status: string; corrected_value?: string } = { status: input.status };
  const correction = input.correctedValue?.trim();
  if (input.status === "confirmed" && correction) {
    body.corrected_value = correction;
  }
  return request<FactUpdateResult>(`/api/facts/${factId}`, { method: "PATCH", body });
}

export function applyFacts(input: FactApplyInput = {}): Promise<FactApplyResult> {
  return request<FactApplyResult>("/api/facts/apply", {
    method: "POST",
    body: { fields: input.fields ?? null, overwrite: input.overwrite ?? false },
  });
}
