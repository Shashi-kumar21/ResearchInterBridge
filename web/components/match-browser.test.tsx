// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";

import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { MatchBrowser } from "./match-browser";

const fetchMock = vi.fn();

function factor(key: string, label: string, status: string, points: number, weight: number, reason: string) {
  return { key, label, weight, credit: weight ? points / weight : 0, points, status, reason, matched: [] };
}

function explanation(score: number, overrides: Record<string, unknown> = {}) {
  return {
    score,
    eligibility: "not_assessed",
    gates: [],
    factors: [
      factor("research_interest", "Research-interest similarity", "met", 35, 35, "Matched 2 of 2 of your terms: machine learning, protein."),
      factor("timeline", "Internship timeline", "not_applicable", 0, 10, "Does not apply to a faculty."),
    ],
    gaps: ["No recruitment evidence is stated."],
    scoring_version: "2",
    evaluated_on: "2026-11-24",
    ...overrides,
  };
}

function faculty(id: number, name: string) {
  return {
    id,
    full_name: name,
    title: null,
    institution: "Example University",
    institution_id: 1,
    department: "Computer Science",
    department_id: 2,
    country: "Germany",
    lab_name: null,
    profile_url: `https://www.cs.example.edu/people/${id}`,
    evidence_urls: [],
    research_interests: ["machine learning"],
    email: null,
    email_status: "unavailable",
    email_source_url: null,
    contact_sendable: false,
    orcid: null,
    recruitment_evidence_url: null,
    highlighted_publications: [],
    publication_count: 0,
    confidence: null,
    last_checked_at: "2026-11-20T00:00:00Z",
    stale: false,
  };
}

const JANE = { faculty: faculty(1, "Jane Doe"), match: explanation(82), shortlist_status: null };
const RAVI = { faculty: faculty(2, "Ravi Kumar"), match: explanation(20), shortlist_status: null };

const PHD = {
  program: {
    id: 9,
    name: "PhD Machine Learning",
    institution: "Example University",
    institution_id: 1,
    department: null,
    department_id: null,
    country: "Germany",
    opportunity_type: null,
    degree_level: "phd",
    research_areas: ["machine learning"],
    official_url: "https://example.edu/phd",
    evidence_urls: [],
    eligibility: [],
    application_method: null,
    opening_date: null,
    closing_date: null,
    start_date: null,
    duration: null,
    stipend: null,
    tuition: null,
    accommodation: null,
    required_documents: [],
    status: "open",
    confidence: null,
    last_checked_at: "2026-11-20T00:00:00Z",
    stale: false,
  },
  match: explanation(90, {
    eligibility: "ineligible",
    gates: [
      { key: "degree", label: "Degree level", status: "fail", reason: "The program states it is for phd students; your degree is msc." },
    ],
  }),
  shortlist_status: null,
};

function json(body: unknown, status = 200): Response {
  return new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function page(items: readonly unknown[], total = items.length, truncated = false) {
  return json({ items, total, limit: 10, offset: 0, truncated });
}

function calls(): { url: string; method: string; body: unknown }[] {
  return fetchMock.mock.calls.map(([input, init]) => ({
    url: String(input),
    method: (init as RequestInit | undefined)?.method ?? "GET",
    body: (init as RequestInit | undefined)?.body ? JSON.parse(String((init as RequestInit).body)) : undefined,
  }));
}

interface RouterState {
  shortlist: unknown[];
  faculty: unknown[];
  excluded?: unknown[];
  /** Every faculty row the fake API knows, so a restore can bring one back. */
  all?: unknown[];
}

function excludedEntry(row: typeof JANE) {
  return {
    id: 100 + row.faculty.id,
    target_type: "faculty",
    target_id: row.faculty.id,
    target_name: row.faculty.full_name,
    status: "excluded",
    snapshot: null,
    current: null,
    created_at: "2026-11-24T10:00:00Z",
    updated_at: "2026-11-24T10:00:00Z",
  };
}

function router(state: RouterState) {
  return (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = String(input);
    const method = init?.method ?? "GET";
    if (url.includes("/api/match/faculty")) {
      const params = new URL(url).searchParams;
      const offset = Number(params.get("offset") ?? 0);
      const limit = Number(params.get("limit") ?? 10);
      return Promise.resolve(
        json({
          items: state.faculty.slice(offset, offset + limit),
          total: state.faculty.length,
          limit,
          offset,
          truncated: false,
        }),
      );
    }
    if (url.includes("/api/match/programs")) {
      return Promise.resolve(page([PHD]));
    }
    if (url.includes("/api/shortlist") && method === "GET") {
      return Promise.resolve(json({ items: [...state.shortlist, ...(state.excluded ?? [])] }));
    }
    if (url.endsWith("/api/shortlist") && method === "POST") {
      const items = JSON.parse(String(init?.body)).items as { type: string; id: number }[];
      state.shortlist = items.map((item, index) => ({
        id: index + 1,
        target_type: item.type,
        target_id: item.id,
        target_name: item.id === 1 ? "Jane Doe" : "Ravi Kumar",
        status: "shortlisted",
        snapshot: { ...explanation(item.id === 1 ? 82 : 20), created_at: "2026-11-24T10:00:00Z" },
        current: true,
        created_at: "2026-11-24T10:00:00Z",
        updated_at: "2026-11-24T10:00:00Z",
      }));
      state.faculty = (state.faculty as typeof JANE[]).map((row) =>
        items.some((item) => item.id === row.faculty.id) ? { ...row, shortlist_status: "shortlisted" } : row,
      );
      return Promise.resolve(json({ changed: items, unchanged: [] }));
    }
    if (url.endsWith("/api/shortlist/exclude")) {
      const items = JSON.parse(String(init?.body)).items as { type: string; id: number }[];
      const gone = (state.faculty as typeof JANE[]).filter((row) =>
        items.some((item) => item.id === row.faculty.id),
      );
      state.faculty = (state.faculty as typeof JANE[]).filter((row) => !gone.includes(row));
      state.excluded = [...(state.excluded ?? []), ...gone.map(excludedEntry)];
      return Promise.resolve(json({ changed: items, unchanged: [] }));
    }
    if (url.endsWith("/api/shortlist/refresh")) {
      state.shortlist = (state.shortlist as Record<string, unknown>[]).map((entry) => ({ ...entry, current: true }));
      return Promise.resolve(json({ changed: [], unchanged: [] }));
    }
    if (method === "DELETE") {
      const id = Number(url.split("/").pop());
      const wasExcluded = (state.excluded ?? []).some(
        (entry) => (entry as { target_id: number }).target_id === id,
      );
      if (wasExcluded) {
        state.excluded = (state.excluded ?? []).filter(
          (entry) => (entry as { target_id: number }).target_id !== id,
        );
        const back = (state.all ?? []).find((row) => (row as typeof JANE).faculty.id === id);
        if (back) {
          state.faculty = [...state.faculty, back];
        }
      } else {
        state.shortlist = [];
      }
      return Promise.resolve(new Response(null, { status: 204 }));
    }
    return Promise.resolve(json({ error: { code: "not_found", message: "no route" } }, 404));
  };
}

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

describe("MatchBrowser", () => {
  it("asks for sign-in without a session", async () => {
    fetchMock.mockResolvedValue(json({ error: { code: "invalid_session", message: "no" } }, 401));
    render(<MatchBrowser />);

    expect(await screen.findByRole("heading", { name: "You are not signed in" })).toBeVisible();
  });

  it("sends the student to the profile when none is saved", async () => {
    // A fresh Response per call: a body can be read only once.
    fetchMock.mockImplementation(() =>
      Promise.resolve(json({ error: { code: "profile_missing", message: "no profile" } }, 404)),
    );
    render(<MatchBrowser />);

    expect(await screen.findByRole("heading", { name: "Save your profile first" })).toBeVisible();
    expect(screen.getByRole("link", { name: "Go to your profile" })).toHaveAttribute("href", "/onboarding");
  });

  it("shows ranked faculty with the score, its reasons and the unknowns", async () => {
    fetchMock.mockImplementation(router({ shortlist: [], faculty: [JANE, RAVI] }));
    render(<MatchBrowser />);

    expect(await screen.findByRole("heading", { name: "Jane Doe" })).toBeVisible();
    expect(screen.getByLabelText("Score 82 of 100")).toBeVisible();
    const card = screen.getByRole("heading", { name: "Jane Doe" }).closest("li") as HTMLElement;
    expect(within(card).getByText(/Matched 2 of 2 of your terms/)).toBeInTheDocument();
    expect(within(card).getByText("35 / 35")).toBeInTheDocument();
    // A factor that does not apply is not shown as a zero.
    expect(within(card).queryByText("Internship timeline")).toBeNull();
    expect(within(card).getByText("No recruitment evidence is stated.")).toBeInTheDocument();
    expect(screen.getByText(/2 ranked · best first · ineligible last/)).toBeVisible();
  });

  it("shows a failed eligibility condition up front on the programs tab", async () => {
    fetchMock.mockImplementation(router({ shortlist: [], faculty: [JANE] }));
    const user = userEvent.setup();
    render(<MatchBrowser />);

    await screen.findByRole("heading", { name: "Jane Doe" });
    await user.click(screen.getByRole("tab", { name: "Programs" }));

    expect(await screen.findByRole("heading", { name: "PhD Machine Learning" })).toBeVisible();
    const failed = screen.getByRole("list", { name: "Failed eligibility conditions" });
    expect(within(failed).getByText(/for phd students; your degree is msc/)).toBeVisible();
    expect(screen.getByText(/Not eligible/)).toBeVisible();
  });

  it("shortlists several selected matches in one request", async () => {
    const state: RouterState = { shortlist: [], faculty: [JANE, RAVI] };
    fetchMock.mockImplementation(router(state));
    const user = userEvent.setup();
    render(<MatchBrowser />);

    await screen.findByRole("heading", { name: "Jane Doe" });
    await user.click(screen.getByRole("checkbox", { name: "Select Jane Doe" }));
    await user.click(screen.getByRole("checkbox", { name: "Select Ravi Kumar" }));
    await user.click(screen.getByRole("button", { name: "Shortlist selected (2)" }));

    expect(await screen.findByText("Shortlisted 2 entries.")).toBeVisible();
    const posts = calls().filter((call) => call.method === "POST" && call.url.endsWith("/api/shortlist"));
    expect(posts).toHaveLength(1);
    expect(posts[0]?.body).toEqual({
      items: [
        { type: "faculty", id: 1 },
        { type: "faculty", id: 2 },
      ],
    });
    const shortlist = screen.getByRole("heading", { name: "My shortlist" }).closest("section") as HTMLElement;
    expect(within(shortlist).getByText(/2 entries · only you can see it/)).toBeVisible();
  });

  it("excludes a match so it leaves the ranked list", async () => {
    fetchMock.mockImplementation(router({ shortlist: [], faculty: [JANE, RAVI] }));
    const user = userEvent.setup();
    render(<MatchBrowser />);

    await screen.findByRole("heading", { name: "Ravi Kumar" });
    await user.click(screen.getByRole("button", { name: "Exclude Ravi Kumar" }));

    expect(await screen.findByText("Ravi Kumar will no longer be suggested.")).toBeVisible();
    const matches = screen.getByRole("heading", { name: "Faculty matches" }).closest("section") as HTMLElement;
    expect(within(matches).queryByRole("heading", { name: "Ravi Kumar" })).toBeNull();
  });

  it("marks an out-of-date saved score and refreshes it", async () => {
    const stale = {
      id: 1,
      target_type: "faculty",
      target_id: 1,
      target_name: "Jane Doe",
      status: "shortlisted",
      snapshot: { ...explanation(70), created_at: "2026-11-20T10:00:00Z" },
      current: false,
      created_at: "2026-11-20T10:00:00Z",
      updated_at: "2026-11-20T10:00:00Z",
    };
    fetchMock.mockImplementation(router({ shortlist: [stale], faculty: [JANE] }));
    const user = userEvent.setup();
    render(<MatchBrowser />);

    expect(await screen.findByText(/Out of date: your profile or the record changed/)).toBeVisible();
    await user.click(screen.getByRole("button", { name: "Refresh saved scores" }));

    expect(await screen.findByText("Saved scores refreshed.")).toBeVisible();
    expect(screen.queryByText(/Out of date/)).toBeNull();
    expect(calls().some((call) => call.url.endsWith("/api/shortlist/refresh"))).toBe(true);
  });

  it("removes a shortlist entry", async () => {
    const entry = {
      id: 1,
      target_type: "faculty",
      target_id: 1,
      target_name: "Jane Doe",
      status: "shortlisted",
      snapshot: null,
      current: null,
      created_at: "2026-11-20T10:00:00Z",
      updated_at: "2026-11-20T10:00:00Z",
    };
    fetchMock.mockImplementation(router({ shortlist: [entry], faculty: [JANE] }));
    const user = userEvent.setup();
    render(<MatchBrowser />);

    await user.click(await screen.findByRole("button", { name: "Remove Jane Doe from your shortlist" }));

    expect(await screen.findByText("Nothing shortlisted yet. Select matches above and shortlist them.")).toBeVisible();
    expect(calls().some((call) => call.method === "DELETE" && call.url.endsWith("/api/shortlist/faculty/1"))).toBe(true);
  });

  it("sends filters and explains a rate limit", async () => {
    const state: RouterState = { shortlist: [], faculty: [JANE] };
    const base = router(state);
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) =>
      String(input).includes("area=")
        ? Promise.resolve(
            new Response(JSON.stringify({ error: { code: "rate_limited", message: "slow down" } }), {
              status: 429,
              headers: { "content-type": "application/json", "retry-after": "120" },
            }),
          )
        : base(input, init),
    );
    const user = userEvent.setup();
    render(<MatchBrowser />);

    await screen.findByRole("heading", { name: "Jane Doe" });
    await user.type(screen.getByLabelText("Research area"), "machine learning");
    await user.click(screen.getByRole("button", { name: "Apply filters" }));

    expect(await screen.findByText("Too many attempts. Try again in 2 minutes.")).toBeVisible();
    await waitFor(() =>
      expect(calls().some((call) => call.url.includes("area=machine+learning"))).toBe(true),
    );
  });

  it("says when the candidate set was truncated", async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL) =>
      Promise.resolve(
        String(input).includes("/api/match/faculty") ? page([JANE], 300, true) : json({ items: [] }),
      ),
    );
    render(<MatchBrowser />);

    expect(await screen.findByText(/More records matched than one request scores/)).toBeVisible();
  });

  it("restores an excluded match from the Excluded list", async () => {
    // QA B1 on #39: an exclusion must be reversible from the screen.
    const state: RouterState = { shortlist: [], faculty: [JANE, RAVI], all: [JANE, RAVI] };
    fetchMock.mockImplementation(router(state));
    const user = userEvent.setup();
    render(<MatchBrowser />);

    await user.click(await screen.findByRole("button", { name: "Exclude Ravi Kumar" }));
    await screen.findByText("Ravi Kumar will no longer be suggested.");
    const excludedSection = screen.getByRole("heading", { name: "Excluded" }).closest("section") as HTMLElement;
    expect(within(excludedSection).getByRole("heading", { name: "Ravi Kumar" })).toBeVisible();

    await user.click(screen.getByRole("button", { name: "Restore Ravi Kumar to your matches" }));

    expect(await screen.findByText("Ravi Kumar is back in your matches.")).toBeVisible();
    const matches = screen.getByRole("heading", { name: "Faculty matches" }).closest("section") as HTMLElement;
    expect(within(matches).getByRole("heading", { name: "Ravi Kumar" })).toBeVisible();
    expect(within(excludedSection).getByText(/Nothing excluded/)).toBeVisible();
    expect(
      calls().some((call) => call.method === "DELETE" && call.url.endsWith("/api/shortlist/faculty/2")),
    ).toBe(true);
  });

  it("offers an undo right after an exclusion", async () => {
    const state: RouterState = { shortlist: [], faculty: [JANE, RAVI], all: [JANE, RAVI] };
    fetchMock.mockImplementation(router(state));
    const user = userEvent.setup();
    render(<MatchBrowser />);

    await user.click(await screen.findByRole("button", { name: "Exclude Ravi Kumar" }));
    await user.click(await screen.findByRole("button", { name: "Undo" }));

    expect(await screen.findByText("Ravi Kumar is back in your matches.")).toBeVisible();
    expect(screen.getAllByRole("heading", { name: "Ravi Kumar" })).toHaveLength(1);
  });

  it("keeps loaded pages and re-scores nothing after a shortlist change", async () => {
    // QA n2 on #39 and the Owner's low-latency priority: patch the card instead.
    const many = Array.from({ length: 12 }, (_, index) => ({
      faculty: faculty(index + 1, `Person ${String(index + 1).padStart(2, "0")}`),
      match: explanation(90 - index),
      shortlist_status: null,
    }));
    fetchMock.mockImplementation(router({ shortlist: [], faculty: many }));
    const user = userEvent.setup();
    render(<MatchBrowser />);

    await screen.findByRole("heading", { name: "Person 01" });
    await user.click(screen.getByRole("button", { name: "Load more matches" }));
    await screen.findByRole("heading", { name: "Person 12" });
    const matchCallsBefore = calls().filter((call) => call.url.includes("/api/match/")).length;

    await user.click(screen.getByRole("button", { name: "Shortlist Person 12" }));

    expect(await screen.findByText("Shortlisted Person 12.")).toBeVisible();
    expect(screen.getByRole("heading", { name: "Person 12" })).toBeVisible();
    expect(screen.getByRole("button", { name: "Shortlist Person 12" })).toHaveTextContent("Shortlisted");
    expect(calls().filter((call) => call.url.includes("/api/match/")).length).toBe(matchCallsBefore);
  });

  it("caps a multi-selection at 50, the API's batch limit", async () => {
    const many = Array.from({ length: 51 }, (_, index) => ({
      faculty: faculty(index + 1, `Person ${String(index + 1).padStart(2, "0")}`),
      match: explanation(90),
      shortlist_status: null,
    }));
    fetchMock.mockImplementation(router({ shortlist: [], faculty: many }));
    const user = userEvent.setup();
    render(<MatchBrowser />);

    await screen.findByRole("heading", { name: "Person 01" });
    for (let index = 0; index < 5; index += 1) {
      await user.click(screen.getByRole("button", { name: "Load more matches" }));
    }
    await screen.findByRole("heading", { name: "Person 51" });
    for (let index = 1; index <= 50; index += 1) {
      await user.click(screen.getByRole("checkbox", { name: `Select Person ${String(index).padStart(2, "0")}` }));
    }

    expect(screen.getByRole("button", { name: "Shortlist selected (50)" })).toBeEnabled();
    expect(screen.getByRole("checkbox", { name: "Select Person 51" })).toBeDisabled();
    expect(screen.getByText(/up to 50 at a time/)).toBeVisible();
  }, 30_000);
});
