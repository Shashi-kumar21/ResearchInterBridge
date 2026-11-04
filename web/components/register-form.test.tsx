// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { RegisterForm } from "./register-form";

const fetchMock = vi.fn();

const CATALOGUE = {
  policy_version: "2026-10-31",
  consents: [
    {
      kind: "personal_data",
      text: "Store my profile and derived facts on this instance.",
      required_for_registration: true,
    },
    {
      kind: "document_processing",
      text: "Extract text and candidate facts from my documents.",
      required_for_registration: true,
    },
    {
      kind: "gmail_access",
      text: "Connect my Gmail account later so I can send approved emails.",
      required_for_registration: false,
    },
  ],
};

const SESSION = {
  account: { user_id: 7, email: "student@example.edu", created_at: "2026-11-01T00:00:00Z" },
  expires_at: "2026-11-04T00:00:00Z",
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

function mockCatalogueThen(session: Response): void {
  fetchMock.mockImplementation((input: RequestInfo | URL) => {
    const url = String(input);
    if (url.endsWith("/api/consents/catalogue")) {
      return Promise.resolve(json(CATALOGUE));
    }
    if (url.endsWith("/api/auth/register")) {
      return Promise.resolve(session);
    }
    return Promise.resolve(new Response(null, { status: 404 }));
  });
}

describe("RegisterForm", () => {
  it("renders the consent text from the API and keeps submit disabled until required consents are ticked", async () => {
    mockCatalogueThen(json(SESSION, 201));
    const user = userEvent.setup();
    render(<RegisterForm />);

    expect(await screen.findByText(/Store my profile and derived facts/)).toBeInTheDocument();
    const submit = screen.getByRole("button", { name: "Create account" });
    expect(submit).toBeDisabled();

    await user.click(screen.getByLabelText(/Store my profile and derived facts/));
    expect(submit).toBeDisabled();

    await user.click(screen.getByLabelText(/Extract text and candidate facts/));
    expect(submit).toBeEnabled();
    // Optional consents are not required to enable the button.
    expect(screen.getByLabelText(/Connect my Gmail account later/)).not.toBeChecked();
  });

  it("submits only the ticked consent kinds and reports the created account", async () => {
    mockCatalogueThen(json(SESSION, 201));
    const user = userEvent.setup();
    render(<RegisterForm />);

    await screen.findByText(/Store my profile and derived facts/);
    await user.type(screen.getByLabelText("Email address"), "student@example.edu");
    await user.type(screen.getByLabelText("Password"), "correct horse battery staple");
    await user.click(screen.getByLabelText(/Store my profile and derived facts/));
    await user.click(screen.getByLabelText(/Extract text and candidate facts/));
    await user.click(screen.getByLabelText(/Connect my Gmail account later/));
    await user.click(screen.getByRole("button", { name: "Create account" }));

    expect(await screen.findByRole("status")).toHaveTextContent("student@example.edu");
    const registerCall = fetchMock.mock.calls.find(([input]) =>
      String(input).endsWith("/api/auth/register"),
    );
    expect(registerCall).toBeDefined();
    const init = registerCall?.[1] as RequestInit;
    expect(JSON.parse(String(init.body))).toEqual({
      email: "student@example.edu",
      password: "correct horse battery staple",
      consents: ["personal_data", "document_processing", "gmail_access"],
    });
  });

  it("shows the duplicate-email error from the API", async () => {
    mockCatalogueThen(
      json(
        { error: { code: "email_taken", message: "an account with this email already exists." } },
        409,
      ),
    );
    const user = userEvent.setup();
    render(<RegisterForm />);

    await screen.findByText(/Store my profile and derived facts/);
    await user.type(screen.getByLabelText("Email address"), "student@example.edu");
    await user.type(screen.getByLabelText("Password"), "correct horse battery staple");
    await user.click(screen.getByLabelText(/Store my profile and derived facts/));
    await user.click(screen.getByLabelText(/Extract text and candidate facts/));
    await user.click(screen.getByRole("button", { name: "Create account" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/already has an account/i);
  });

  it("does not let anyone submit consent when the texts could not be loaded", async () => {
    fetchMock.mockResolvedValue(new Response(null, { status: 503 }));
    render(<RegisterForm />);

    expect(await screen.findByRole("alert")).toHaveTextContent(/status 503/i);
    expect(screen.getByRole("button", { name: "Create account" })).toBeDisabled();
    expect(
      screen.getByRole("button", { name: "Try loading the consent texts again" }),
    ).toBeInTheDocument();
  });
});
