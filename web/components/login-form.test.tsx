// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderToString } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { LoginForm } from "./login-form";

const fetchMock = vi.fn();

const SESSION = {
  account: { user_id: 7, email: "student@example.edu", created_at: "2026-11-01T00:00:00Z" },
  expires_at: "2026-11-04T00:00:00Z",
};

function json(body: unknown, status = 200, headers: Record<string, string> = {}): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json", ...headers },
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

async function submitCredentials(): Promise<void> {
  const user = userEvent.setup();
  render(<LoginForm />);
  await user.type(screen.getByLabelText("Email address"), "student@example.edu");
  await user.type(screen.getByLabelText("Password"), "correct horse battery staple");
  await user.click(screen.getByRole("button", { name: "Sign in" }));
}

describe("LoginForm", () => {
  it("cannot submit natively before hydration", () => {
    // Server-rendered HTML has no React handler. The submit button must already
    // be disabled, and the form must never be a GET: otherwise a click (or
    // JavaScript disabled) puts the password in the URL.
    const html = renderToString(<LoginForm />);

    expect(html).toContain('method="post"');
    expect(html).not.toMatch(/method="get"/i);
    expect(html).toMatch(/<button[^>]*disabled[^>]*>Sign in<\/button>/);
  });

  it("reports the signed-in account", async () => {
    fetchMock.mockResolvedValue(json(SESSION));
    await submitCredentials();

    expect(await screen.findByRole("status")).toHaveTextContent("student@example.edu");
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit];
    expect(url.endsWith("/api/auth/login")).toBe(true);
    expect(init.credentials).toBe("include");
  });

  it("shows one message for a wrong password or an unknown email", async () => {
    fetchMock.mockResolvedValue(
      json(
        { error: { code: "invalid_credentials", message: "invalid email or password." } },
        401,
      ),
    );
    await submitCredentials();

    expect(await screen.findByRole("alert")).toHaveTextContent("Email or password is incorrect.");
  });

  it("quotes Retry-After when the account is locked", async () => {
    fetchMock.mockResolvedValue(
      json(
        { error: { code: "account_locked", message: "too many failed attempts." } },
        429,
        { "retry-after": "900" },
      ),
    );
    await submitCredentials();

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "Too many attempts. Try again in 15 minutes.",
    );
  });
});
