// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DraftStudio } from "./draft-studio";

const fetchMock = vi.fn();

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

function version(no: number, overrides: Record<string, unknown> = {}) {
  return {
    version_no: no,
    source: no === 1 ? "generated" : "edited",
    subject: "Research internship inquiry: Protein Folding",
    body: "Dear Professor Jane Doe,\n\nYour paper caught my interest.",
    recipient: "jane.doe@cs.example.edu",
    cv_version_id: 7,
    evidence: [{ claim: "Paper: Protein Folding", source: "https://doi.org/10.1234/pf" }],
    flags: [],
    created_at: "2026-11-28T10:00:00Z",
    ...overrides,
  };
}

function draft(overrides: Record<string, unknown> = {}) {
  const current = (overrides.current as ReturnType<typeof version>) ?? version(1);
  return {
    id: 5,
    target_type: "faculty",
    target_id: 11,
    target_name: "Jane Doe",
    current,
    history: [current],
    lint: [] as { code: string; level: string; message: string }[],
    approval: null as { version_no: number; content_sha256: string; approved_at: string } | null,
    created_at: "2026-11-28T10:00:00Z",
    updated_at: "2026-11-28T10:00:00Z",
    ...overrides,
  };
}

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), { status, headers: { "content-type": "application/json" } });
}

function calls(): { url: string; method: string; body: unknown }[] {
  return fetchMock.mock.calls.map(([input, init]) => ({
    url: String(input),
    method: (init as RequestInit | undefined)?.method ?? "GET",
    body: (init as RequestInit | undefined)?.body ? JSON.parse(String((init as RequestInit).body)) : undefined,
  }));
}

interface State {
  drafts: unknown[];
  shortlist: unknown[];
  cv: unknown[];
  current: ReturnType<typeof draft>;
  onApprove?: () => Response;
  onSave?: (body: { subject: string; body: string }) => Response;
}

function router(state: State) {
  return (input: RequestInfo | URL, init?: RequestInit): Promise<Response> => {
    const url = String(input);
    const method = init?.method ?? "GET";
    const body = init?.body ? JSON.parse(String(init.body)) : undefined;
    if (url.endsWith("/api/drafts") && method === "GET") {
      return Promise.resolve(json({ drafts: state.drafts }));
    }
    if (url.includes("/api/shortlist")) {
      return Promise.resolve(json({ items: state.shortlist }));
    }
    if (url.endsWith("/api/cv/versions")) {
      return Promise.resolve(json({ versions: state.cv }));
    }
    if (url.endsWith("/api/drafts") && method === "POST") {
      return Promise.resolve(json(state.current));
    }
    if (url.endsWith("/approve")) {
      if (state.onApprove) {
        return Promise.resolve(state.onApprove());
      }
      state.current = { ...state.current, approval: { version_no: state.current.current.version_no, content_sha256: "x", approved_at: "2026-11-28T11:00:00Z" } };
      return Promise.resolve(json(state.current));
    }
    if (url.endsWith("/approval") && method === "DELETE") {
      state.current = { ...state.current, approval: null };
      return Promise.resolve(json(state.current));
    }
    if (/\/api\/drafts\/\d+$/.test(url) && method === "PUT") {
      if (state.onSave) {
        return Promise.resolve(state.onSave(body));
      }
      const next = version(state.current.current.version_no + 1, { subject: body.subject, body: body.body });
      state.current = { ...state.current, current: next, history: [next, ...state.current.history], approval: null };
      return Promise.resolve(json(state.current));
    }
    if (/\/api\/drafts\/\d+$/.test(url)) {
      return Promise.resolve(json(state.current));
    }
    return Promise.resolve(json({ error: { code: "not_found", message: "no route" } }, 404));
  };
}

function ready(overrides: Partial<State> = {}): State {
  return { drafts: [], shortlist: [TARGET], cv: [{ id: 7 }], current: draft(), ...overrides };
}

async function openJane(user: ReturnType<typeof userEvent.setup>) {
  await user.click(await screen.findByRole("button", { name: /draft for Jane Doe/ }));
  await screen.findByRole("heading", { name: "Email to Jane Doe" });
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

describe("DraftStudio", () => {
  it("asks for sign-in without a session", async () => {
    fetchMock.mockImplementation(() =>
      Promise.resolve(json({ error: { code: "invalid_session", message: "no" } }, 401)),
    );
    render(<DraftStudio />);

    expect(await screen.findByRole("heading", { name: "You are not signed in" })).toBeVisible();
  });

  it("explains an empty shortlist and a missing CV", async () => {
    fetchMock.mockImplementation(router(ready({ shortlist: [], cv: [] })));
    render(<DraftStudio />);

    expect(await screen.findByRole("link", { name: "Shortlist a professor" })).toHaveAttribute("href", "/matches");
    expect(screen.getByRole("link", { name: "Prepare your CV" })).toHaveAttribute("href", "/cv");
  });

  it("creates a draft and renders HTML in the body as plain text", async () => {
    const hostile = draft({ current: version(1, { body: '<img src=x onerror="alert(1)"> Hello' }) });
    fetchMock.mockImplementation(router(ready({ current: hostile })));
    const user = userEvent.setup();
    render(<DraftStudio />);

    await openJane(user);

    expect(screen.getByLabelText("Message")).toHaveValue('<img src=x onerror="alert(1)"> Hello');
    expect(document.querySelector("img")).toBeNull();
    expect(screen.getByText("No issues found. This draft passes every deliverability check.")).toBeVisible();
    expect(calls().some((call) => call.method === "POST" && call.url.endsWith("/api/drafts"))).toBe(true);
  });

  it("shows blocking and warning checks inline and keeps approval disabled while blocked", async () => {
    const blocked = draft({
      lint: [
        { code: "spam_phrase", level: "block", message: "Remove wording that reads as spam: 'click here'." },
        { code: "exclamations", level: "warn", message: "Use fewer exclamation marks (2 found)." },
      ],
    });
    fetchMock.mockImplementation(router(ready({ current: blocked })));
    const user = userEvent.setup();
    render(<DraftStudio />);
    await openJane(user);

    const checks = screen.getByRole("list", { name: "Deliverability checks" });
    expect(checks).toHaveTextContent("Must fix: Remove wording that reads as spam: 'click here'.");
    expect(checks).toHaveTextContent("Suggestion: Use fewer exclamation marks (2 found).");
    expect(screen.getByRole("button", { name: "Approve this exact version" })).toBeDisabled();
  });

  it("approves, then an edit clears the approval and says so", async () => {
    const state = ready();
    fetchMock.mockImplementation(router(state));
    const user = userEvent.setup();
    render(<DraftStudio />);
    await openJane(user);

    await user.click(screen.getByRole("button", { name: "Approve this exact version" }));
    expect(await screen.findByText(/Approved version 1 on/)).toBeVisible();
    expect(calls().find((call) => call.url.endsWith("/approve"))?.body).toEqual({ version_no: 1 });

    await user.type(screen.getByLabelText("Message"), " Thanks.");
    await user.click(screen.getByRole("button", { name: "Save changes" }));

    expect(await screen.findByText(/Your edit cleared the approval; review and approve again./)).toBeVisible();
    expect(screen.getByRole("button", { name: "Approve this exact version" })).toBeEnabled();
  });

  it("reloads the draft when it changed elsewhere before approval", async () => {
    const state = ready();
    state.onApprove = () => {
      state.current = draft({ current: version(2, { body: "Changed elsewhere." }) });
      state.onApprove = undefined;
      return json({ error: { code: "draft_changed", message: "changed" } }, 409);
    };
    fetchMock.mockImplementation(router(state));
    const user = userEvent.setup();
    render(<DraftStudio />);
    await openJane(user);

    await user.click(screen.getByRole("button", { name: "Approve this exact version" }));

    expect(await screen.findByText(/This draft changed since you reviewed it/)).toBeVisible();
    expect(screen.getByLabelText("Message")).toHaveValue("Changed elsewhere.");
  });

  it("withdraws an approval and shows the version history", async () => {
    const approved = draft({
      current: version(2),
      history: [version(2), version(1, { body: "First wording." })],
      approval: { version_no: 2, content_sha256: "x", approved_at: "2026-11-28T11:00:00Z" },
    });
    fetchMock.mockImplementation(router(ready({ current: approved, drafts: [{ id: 5, target_id: 11, target_name: "Jane Doe", subject: "s", version_no: 2, recipient: "jane.doe@cs.example.edu", flag_count: 0, approved: true, updated_at: "2026-11-28T11:00:00Z" }] })));
    const user = userEvent.setup();
    render(<DraftStudio />);

    expect(await screen.findByText("Approved")).toBeVisible();
    await openJane(user);
    await user.click(screen.getByText("Version history (2)"));
    await user.click(screen.getByRole("button", { name: "View version 1" }));
    expect(screen.getByText("First wording.")).toBeVisible();

    await user.click(screen.getByRole("button", { name: "Withdraw approval" }));
    expect(await screen.findByText("Approval withdrawn.")).toBeVisible();
  });

  it("blocks approval and explains an unverified recipient", async () => {
    const noEmail = draft({ current: version(1, { recipient: null }) });
    fetchMock.mockImplementation(router(ready({ current: noEmail })));
    const user = userEvent.setup();
    render(<DraftStudio />);
    await openJane(user);

    expect(screen.getByText(/To: no verified email yet/)).toBeVisible();
    expect(screen.getByRole("button", { name: "Approve this exact version" })).toBeDisabled();
  });

  it("keeps unsaved changes by blocking another draft and never polls", async () => {
    fetchMock.mockImplementation(router(ready()));
    const user = userEvent.setup();
    render(<DraftStudio />);
    await openJane(user);
    await user.type(screen.getByLabelText("Subject"), " now");

    expect(screen.getByRole("button", { name: /draft for Jane Doe/ })).toBeDisabled();
    expect(screen.getByText("Save or discard your changes before opening another draft.")).toBeVisible();
    const count = fetchMock.mock.calls.length;
    await new Promise((resolve) => setTimeout(resolve, 50));
    await waitFor(() => expect(fetchMock.mock.calls.length).toBe(count));
  });
});
