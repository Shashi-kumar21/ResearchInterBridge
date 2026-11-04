// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ConsentManager } from "./consent-manager";

const fetchMock = vi.fn();

const CONSENTS = {
  policy_version: "2026-10-31",
  consents: [
    { kind: "document_processing", text: "Extract text from my documents.", granted: true },
    { kind: "gmail_access", text: "Connect my Gmail account later.", granted: false },
    { kind: "personal_data", text: "Store my profile on this instance.", granted: true },
  ],
};

const UPDATED = {
  policy_version: "2026-10-31",
  consents: [
    { kind: "document_processing", text: "Extract text from my documents.", granted: true },
    { kind: "gmail_access", text: "Connect my Gmail account later.", granted: true },
    { kind: "personal_data", text: "Store my profile on this instance.", granted: true },
  ],
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("ConsentManager", () => {
  it("asks the visitor to sign in when there is no session", async () => {
    fetchMock.mockResolvedValue(
      json({ error: { code: "invalid_session", message: "authentication required." } }, 401),
    );
    render(<ConsentManager />);

    expect(await screen.findByRole("heading", { name: "You are not signed in" })).toBeVisible();
    expect(screen.getByRole("link", { name: "Sign in" })).toHaveAttribute("href", "/login");
  });

  it("shows the server's ledger and only offers to grant what is missing", async () => {
    fetchMock.mockResolvedValue(json(CONSENTS));
    render(<ConsentManager />);

    expect(await screen.findByRole("heading", { name: "Gmail access" })).toBeVisible();
    expect(screen.getByText("2026-10-31")).toBeInTheDocument();
    expect(screen.getAllByText("Granted")).toHaveLength(2);
    expect(screen.getByRole("button", { name: "Grant this consent" })).toBeInTheDocument();
  });

  it("records the missing consent and refreshes the ledger", async () => {
    fetchMock
      .mockResolvedValueOnce(json(CONSENTS))
      .mockResolvedValueOnce(
        json({ policy_version: "2026-10-31", kind: "gmail_access", already_granted: false }),
      )
      .mockResolvedValueOnce(json(UPDATED));
    const user = userEvent.setup();
    render(<ConsentManager />);

    await screen.findByRole("heading", { name: "Gmail access" });
    await user.click(screen.getByRole("button", { name: "Grant this consent" }));

    expect(await screen.findByText(/Consent recorded/)).toBeVisible();
    expect(screen.getAllByText("Granted")).toHaveLength(3);
    const grantCall = fetchMock.mock.calls.find(([, init]) => (init as RequestInit)?.method === "POST");
    const init = grantCall?.[1] as RequestInit;
    expect(JSON.parse(String(init.body))).toEqual({ kind: "gmail_access" });
  });

  it("keeps a recorded grant visible when the follow-up refresh fails", async () => {
    fetchMock
      .mockResolvedValueOnce(json(CONSENTS))
      .mockResolvedValueOnce(
        json({ policy_version: "2026-10-31", kind: "gmail_access", already_granted: false }),
      )
      .mockResolvedValueOnce(new Response(null, { status: 503 }));
    const user = userEvent.setup();
    render(<ConsentManager />);

    await screen.findByRole("heading", { name: "Gmail access" });
    await user.click(screen.getByRole("button", { name: "Grant this consent" }));

    // The grant is a server row; a failed list refresh must not make it look lost.
    expect(await screen.findByText(/Consent recorded/)).toBeVisible();
    expect(screen.getAllByText("Granted")).toHaveLength(3);
    expect(screen.queryByRole("alert")).toBeNull();
  });

  it("returns to the signed-out state when the session expires while granting", async () => {
    fetchMock
      .mockResolvedValueOnce(json(CONSENTS))
      .mockResolvedValueOnce(
        json({ error: { code: "invalid_session", message: "authentication required." } }, 401),
      );
    const user = userEvent.setup();
    render(<ConsentManager />);

    await screen.findByRole("heading", { name: "Gmail access" });
    await user.click(screen.getByRole("button", { name: "Grant this consent" }));

    expect(await screen.findByRole("heading", { name: "You are not signed in" })).toBeVisible();
  });

  it("signs out and returns to the signed-out state", async () => {
    fetchMock
      .mockResolvedValueOnce(json(CONSENTS))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    const user = userEvent.setup();
    render(<ConsentManager />);

    await screen.findByRole("heading", { name: "Gmail access" });
    await user.click(screen.getByRole("button", { name: "Sign out" }));

    expect(await screen.findByRole("heading", { name: "You are not signed in" })).toBeVisible();
    const logoutCall = fetchMock.mock.calls.find(([input]) =>
      String(input).endsWith("/api/auth/logout"),
    );
    expect(logoutCall).toBeDefined();
  });

  it("treats a 401 while signing out as already signed out", async () => {
    fetchMock
      .mockResolvedValueOnce(json(CONSENTS))
      .mockResolvedValueOnce(
        json({ error: { code: "invalid_session", message: "authentication required." } }, 401),
      );
    const user = userEvent.setup();
    render(<ConsentManager />);

    await screen.findByRole("heading", { name: "Gmail access" });
    await user.click(screen.getByRole("button", { name: "Sign out" }));

    expect(await screen.findByRole("heading", { name: "You are not signed in" })).toBeVisible();
  });

  it("offers a retry when the ledger cannot be loaded", async () => {
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 503 }));
    render(<ConsentManager />);

    expect(await screen.findByRole("alert")).toHaveTextContent(/status 503/i);
    expect(screen.getByRole("button", { name: "Try again" })).toBeInTheDocument();
  });
});
