// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";

import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ProfileForm } from "./profile-form";

const fetchMock = vi.fn();

const PROFILE = {
  user_id: 1,
  full_name: "Ada Lovelace",
  contact_email: "ada@example.edu",
  preferred_name: "Ada",
  phone: null,
  institution: "Example University",
  department: "Computer Science",
  country: "United Kingdom",
  degree_type: "bs",
  current_year: 3,
  expected_graduation: "2027-06-30",
  gpa: 3.8,
  gpa_scale: 4,
  coursework: ["Algorithms", "Databases"],
  skills: ["Python"],
  research_keywords: ["graph theory"],
  target_countries: ["Germany"],
  internship_start: "2027-05-01",
  internship_end: "2027-07-31",
  funding_required: true,
  consent_given_at: "2026-11-01T00:00:00Z",
  created_at: "2026-11-01T00:00:00Z",
  updated_at: "2026-11-02T00:00:00Z",
  completeness_percent: 76,
  completeness_missing: ["grading scale"],
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function missingProfile(): Response {
  return json({ error: { code: "profile_missing", message: "profile not found." } }, 404);
}

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("ProfileForm", () => {
  it("asks the visitor to sign in when the session is gone", async () => {
    fetchMock.mockResolvedValue(
      json({ error: { code: "invalid_session", message: "authentication required." } }, 401),
    );
    render(<ProfileForm />);

    expect(await screen.findByRole("heading", { name: "You are not signed in" })).toBeVisible();
    expect(screen.getByRole("link", { name: "Sign in" })).toHaveAttribute("href", "/login");
  });

  it("treats a missing profile as a new form, not an error", async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      if (String(input).endsWith("/api/profile")) {
        return Promise.resolve(missingProfile());
      }
      return Promise.resolve(json({}));
    });
    render(<ProfileForm />);

    expect(await screen.findByText(/No profile is saved yet/)).toBeVisible();
    expect(screen.queryByRole("alert")).toBeNull();
    expect(screen.getByLabelText("Full name")).toHaveValue("");
    expect(screen.getByLabelText("Contact email")).toHaveValue("");
  });

  it("sends a full replacement and parses lists and blank fields", async () => {
    let saved = false;
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).endsWith("/api/profile") && init?.method === "PUT") {
        saved = true;
        return Promise.resolve(
          json({
            ...PROFILE,
            full_name: "Ada Lovelace",
            contact_email: "ada@example.edu",
            preferred_name: null,
            institution: null,
            department: null,
            country: null,
            degree_type: "bs",
            current_year: null,
            expected_graduation: null,
            gpa: null,
            gpa_scale: null,
            coursework: ["Algorithms", "Databases"],
            skills: [],
            research_keywords: [],
            target_countries: [],
            internship_start: null,
            internship_end: null,
            funding_required: null,
            completeness_percent: 44,
            completeness_missing: ["current institution", "technical skills"],
          }),
        );
      }
      return Promise.resolve(missingProfile());
    });
    const user = userEvent.setup();
    render(<ProfileForm />);

    await screen.findByText(/No profile is saved yet/);
    await user.type(screen.getByLabelText("Full name"), "Ada Lovelace");
    await user.type(screen.getByLabelText("Contact email"), "ada@example.edu");
    await user.selectOptions(screen.getByLabelText("Degree"), "bs");
    await user.type(screen.getByLabelText("Relevant coursework"), "Algorithms, Databases");
    await user.click(screen.getByRole("button", { name: "Save profile" }));

    expect(await screen.findByText(/Profile saved/)).toBeVisible();
    expect(saved).toBe(true);
    const putCall = fetchMock.mock.calls.find(
      ([, init]) => (init as RequestInit | undefined)?.method === "PUT",
    );
    expect(putCall).toBeDefined();
    expect(putCall?.[0]).toMatch(/\/api\/profile$/);
    expect(JSON.parse(String((putCall?.[1] as RequestInit).body))).toEqual({
      full_name: "Ada Lovelace",
      contact_email: "ada@example.edu",
      preferred_name: null,
      phone: null,
      institution: null,
      department: null,
      country: null,
      degree_type: "bs",
      current_year: null,
      expected_graduation: null,
      gpa: null,
      gpa_scale: null,
      coursework: ["Algorithms", "Databases"],
      skills: [],
      research_keywords: [],
      target_countries: [],
      internship_start: null,
      internship_end: null,
      funding_required: null,
    });
    expect(screen.getByRole("progressbar", { name: "Profile completeness" })).toHaveAttribute(
      "value",
      "44",
    );
  });

  it("shows a stored profile and reports the server's validation message", async () => {
    fetchMock
      .mockResolvedValueOnce(json(PROFILE))
      .mockResolvedValueOnce(
        json(
          {
            error: {
              code: "invalid_profile",
              message: "gpa (5) cannot exceed gpa_scale (4.0).",
            },
          },
          400,
        ),
      );
    const user = userEvent.setup();
    render(<ProfileForm />);

    expect(await screen.findByLabelText("Full name")).toHaveValue("Ada Lovelace");
    expect(screen.getByLabelText("Institution")).toHaveValue("Example University");
    expect(screen.getByText(/76% complete/)).toBeVisible();
    expect(screen.getByText(/Still missing: grading scale/)).toBeVisible();

    await user.clear(screen.getByLabelText("CGPA / GPA"));
    await user.type(screen.getByLabelText("CGPA / GPA"), "5");
    await user.click(screen.getByRole("button", { name: "Save profile" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/cannot exceed/);
  });

  it("offers a retry when the profile cannot be loaded", async () => {
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 503 })).mockResolvedValueOnce(
      json(PROFILE),
    );
    const user = userEvent.setup();
    render(<ProfileForm />);

    expect(await screen.findByRole("alert")).toHaveTextContent(/status 503/i);
    await user.click(screen.getByRole("button", { name: "Try again" }));

    expect(await screen.findByLabelText("Full name")).toHaveValue("Ada Lovelace");
  });
});
