// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";

import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CvStudio } from "./cv-studio";

const fetchMock = vi.fn();

const CV_DOC = {
  id: 3,
  kind: "cv",
  display_name: "asha-cv.pdf",
  media_type: "application/pdf",
  size_bytes: 1200,
  status: "parsed",
  sha256: "abc",
  uploaded_at: "2026-11-20T10:00:00Z",
  parsed_at: "2026-11-20T10:01:00Z",
  parse_error: null,
};

const SECTIONS = [
  { kind: "preamble", heading: null, bullets: ["Asha Rao"] },
  { kind: "skills", heading: "Skills", bullets: ["C++"] },
];

const ORIGINAL = {
  id: 7,
  document_id: 3,
  kind: "original",
  parent_version_id: null,
  target_type: null,
  target_id: null,
  sections: SECTIONS,
  created_at: "2026-11-20T10:02:00Z",
  approved_at: null,
};

const TARGET = {
  id: 1,
  target_type: "faculty",
  target_id: 11,
  target_name: "Jane Doe",
  status: "shortlisted",
  snapshot: null,
  current: null,
  created_at: "2026-11-20T10:00:00Z",
  updated_at: "2026-11-20T10:00:00Z",
};

function suggestion(id: number, overrides: Record<string, unknown> = {}) {
  return {
    id,
    kind: "surface_skill",
    section: 1,
    bullet: null,
    before: [],
    after: ["PyTorch"],
    reason: "'PyTorch' is in your confirmed profile and matches Jane Doe's work.",
    evidence: ["your profile (skills): PyTorch", "Jane Doe: pytorch"],
    status: "pending",
    edited_text: null,
    ...overrides,
  };
}

function run(id: number, suggestions: unknown[]) {
  return {
    id,
    version_id: 7,
    target_type: "faculty",
    target_id: 11,
    target_name: "Jane Doe",
    rules_version: "1",
    created_at: "2026-11-26T10:00:00Z",
    suggestions,
  };
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function calls(): { url: string; method: string; body: unknown }[] {
  return fetchMock.mock.calls.map(([input, init]) => ({
    url: String(input),
    method: (init as RequestInit | undefined)?.method ?? "GET",
    body: (init as RequestInit | undefined)?.body
      ? JSON.parse(String((init as RequestInit).body))
      : undefined,
  }));
}

interface State {
  documents: unknown[];
  versions: unknown[];
  shortlist: unknown[];
  run: ReturnType<typeof run>;
  approve?: () => Response;
  decide?: (id: number, body: { status: string; edited_text?: string }) => Response;
}

function router(state: State) {
  return (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = String(input);
    const method = init?.method ?? "GET";
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    if (url.endsWith("/api/documents")) {
      return Promise.resolve(json({ documents: state.documents }));
    }
    if (url.endsWith("/api/cv/versions")) {
      return Promise.resolve(json({ versions: state.versions }));
    }
    if (url.includes("/api/shortlist")) {
      return Promise.resolve(json({ items: state.shortlist }));
    }
    if (url.endsWith("/api/cv/originals")) {
      state.versions = [ORIGINAL];
      return Promise.resolve(json(ORIGINAL));
    }
    if (url.endsWith("/api/cv/analyses") && method === "POST") {
      return Promise.resolve(json(state.run));
    }
    const decide = url.match(/\/api\/cv\/suggestions\/(\d+)$/);
    if (decide && method === "PATCH") {
      const id = Number(decide[1]);
      if (state.decide) {
        return Promise.resolve(state.decide(id, body));
      }
      const current = state.run.suggestions.find((s) => (s as { id: number }).id === id) as object;
      const saved = { ...current, status: body.status, edited_text: body.edited_text ?? null };
      state.run = {
        ...state.run,
        suggestions: state.run.suggestions.map((s) => ((s as { id: number }).id === id ? saved : s)),
      };
      return Promise.resolve(json(saved));
    }
    if (url.endsWith("/preview")) {
      const applied = state.run.suggestions.filter((s) =>
        ["accepted", "edited"].includes((s as { status: string }).status),
      ).length;
      return Promise.resolve(
        json({
          sections: SECTIONS,
          applied,
          diff: applied
            ? [
                { op: "same", line: "## Skills" },
                { op: "same", line: "C++" },
                { op: "added", line: "PyTorch" },
              ]
            : [
                { op: "same", line: "## Skills" },
                { op: "same", line: "C++" },
              ],
        }),
      );
    }
    if (url.endsWith("/approve")) {
      return Promise.resolve(
        state.approve?.() ??
          json({
            version: { ...ORIGINAL, id: 8, kind: "tailored", parent_version_id: 7, target_type: "faculty", target_id: 11, approved_at: "2026-11-26T10:05:00Z" },
            diff: [],
          }),
      );
    }
    return Promise.resolve(json({ error: { code: "not_found", message: "no route" } }, 404));
  };
}

function ready(overrides: Partial<State> = {}): State {
  return {
    documents: [CV_DOC],
    versions: [ORIGINAL],
    shortlist: [TARGET],
    run: run(50, [suggestion(1), suggestion(2, { kind: "flag_vague", after: ["Worked on pipelines"], before: ["Worked on pipelines"] })]),
    ...overrides,
  };
}

async function openJane(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole("button", { name: "Jane Doe" }));
  await screen.findByRole("heading", { name: "3. Suggestions for Jane Doe" });
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

describe("CvStudio", () => {
  it("asks for sign-in without a session", async () => {
    fetchMock.mockImplementation(() =>
      Promise.resolve(json({ error: { code: "invalid_session", message: "no" } }, 401)),
    );
    render(<CvStudio />);

    expect(await screen.findByRole("heading", { name: "You are not signed in" })).toBeVisible();
  });

  it("points to the upload page when no CV exists", async () => {
    fetchMock.mockImplementation(router(ready({ documents: [], versions: [] })));
    render(<CvStudio />);

    expect(await screen.findByRole("link", { name: "Upload your CV" })).toHaveAttribute("href", "/documents");
  });

  it("prepares the original and then asks for a shortlist when it is empty", async () => {
    fetchMock.mockImplementation(router(ready({ versions: [], shortlist: [] })));
    const user = userEvent.setup();
    render(<CvStudio />);

    await user.click(await screen.findByRole("button", { name: "Use this CV" }));

    expect(await screen.findByRole("link", { name: "Shortlist a professor or program" })).toHaveAttribute(
      "href",
      "/matches",
    );
    expect(calls().some((call) => call.url.endsWith("/api/cv/originals") && call.method === "POST")).toBe(true);
  });

  it("shows each suggestion with evidence and the before-and-after diff", async () => {
    fetchMock.mockImplementation(router(ready()));
    const user = userEvent.setup();
    render(<CvStudio />);

    await openJane(user);

    expect(screen.getByText("Would add: “PyTorch”")).toBeVisible();
    expect(screen.getAllByText("Evidence (2)")).toHaveLength(2);
    expect(screen.getByText(/No change yet/)).toBeVisible();
  });

  it("saves a decision without reloading the analysis, and undoes it in one click", async () => {
    const state = ready();
    fetchMock.mockImplementation(router(state));
    const user = userEvent.setup();
    render(<CvStudio />);
    await openJane(user);
    const analysesBefore = calls().filter((call) => call.url.endsWith("/api/cv/analyses")).length;

    await user.click(screen.getByRole("button", { name: "Accept Add a confirmed skill 1" }));

    const list = within(screen.getByRole("list", { name: "Changes against your original CV" }));
    expect(await list.findByText("PyTorch")).toBeVisible();
    expect(screen.getByText(/1 change accepted/)).toBeVisible();
    expect(calls().filter((call) => call.url.endsWith("/api/cv/analyses")).length).toBe(analysesBefore);

    await user.click(screen.getByRole("button", { name: "Undo decision on Add a confirmed skill 1" }));

    expect(await screen.findByText(/0 changes accepted/)).toBeVisible();
    expect(calls().at(-2)?.body).toEqual({ status: "pending" });
  });

  it("sends the student's own wording for an edit", async () => {
    fetchMock.mockImplementation(router(ready()));
    const user = userEvent.setup();
    render(<CvStudio />);
    await openJane(user);

    await user.click(screen.getByRole("button", { name: "Edit Line with no result 2" }));
    const box = screen.getByLabelText("In your own words");
    await user.clear(box);
    await user.type(box, "Built pipelines for 3 labs");
    await user.click(screen.getByRole("button", { name: "Save my wording" }));

    expect(await screen.findByText("Your wording: “Built pipelines for 3 labs”")).toBeVisible();
    expect(calls().find((call) => call.method === "PATCH")?.body).toEqual({
      status: "edited",
      edited_text: "Built pipelines for 3 labs",
    });
  });

  it("explains a 409 on approval and lists a successful approval", async () => {
    const state = ready({
      approve: () =>
        json({ error: { code: "cv_nothing_accepted", message: "accept something" } }, 409),
    });
    fetchMock.mockImplementation(router(state));
    const user = userEvent.setup();
    render(<CvStudio />);
    await openJane(user);

    await user.click(screen.getByRole("button", { name: "Approve this version for Jane Doe" }));
    expect(await screen.findByText("Accept or edit at least one suggestion before approving.")).toBeVisible();

    state.approve = undefined;
    await user.click(screen.getByRole("button", { name: "Accept Add a confirmed skill 1" }));
    await screen.findByText(/1 change accepted/);
    await user.click(screen.getByRole("button", { name: "Approve this version for Jane Doe" }));

    expect(await screen.findByText(/Approved a CV version for Jane Doe/)).toBeVisible();
    expect(screen.getByRole("heading", { name: /Version 8/ })).toBeVisible();
  });

  it("reloads the latest analysis when a suggestion was refreshed", async () => {
    const state = ready();
    state.decide = () => {
      state.run = run(51, [suggestion(9)]);
      state.decide = undefined;
      return json({ error: { code: "cv_suggestion_missing", message: "gone" } }, 404);
    };
    fetchMock.mockImplementation(router(state));
    const user = userEvent.setup();
    render(<CvStudio />);
    await openJane(user);

    await user.click(screen.getByRole("button", { name: "Accept Add a confirmed skill 1" }));

    expect(await screen.findByRole("button", { name: "Accept Add a confirmed skill 9" })).toBeVisible();
    expect(screen.getByText(/These suggestions were refreshed/)).toBeVisible();
  });

  it("never polls after loading", async () => {
    fetchMock.mockImplementation(router(ready()));
    render(<CvStudio />);
    await screen.findByRole("button", { name: "Jane Doe" });
    const count = fetchMock.mock.calls.length;

    await new Promise((resolve) => setTimeout(resolve, 50));

    await waitFor(() => expect(fetchMock.mock.calls.length).toBe(count));
  });
});
