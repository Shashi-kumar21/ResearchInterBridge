// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";

import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { FactReview } from "./fact-review";

const fetchMock = vi.fn();

const GROUPED = {
  groups: [
    {
      field: "contact_email",
      has_conflict: true,
      confirmed: [],
      pending: [
        {
          id: 1,
          field: "contact_email",
          value: "ada@example.edu",
          source: "document:1",
          source_name: "cv.pdf",
          status: "pending",
          confidence: 0.9,
        },
        {
          id: 2,
          field: "contact_email",
          value: "ada@old.example.edu",
          source: "document:2",
          source_name: "old-cv.pdf",
          status: "pending",
          confidence: null,
        },
      ],
    },
    {
      field: "gpa",
      has_conflict: false,
      confirmed: [
        {
          id: 3,
          field: "gpa",
          value: "3.8/4.0",
          source: "document:1",
          source_name: "cv.pdf",
          status: "confirmed",
          confidence: 0.8,
        },
      ],
      pending: [],
    },
  ],
};

const UPDATED = {
  groups: [
    {
      field: "contact_email",
      has_conflict: false,
      confirmed: [
        {
          id: 1,
          field: "contact_email",
          value: "ada@new.example.edu",
          source: "document:1",
          source_name: "cv.pdf",
          status: "confirmed",
          confidence: 0.9,
        },
      ],
      pending: [],
    },
    GROUPED.groups[1],
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

describe("FactReview", () => {
  it("asks the visitor to sign in without a session", async () => {
    fetchMock.mockResolvedValue(
      json({ error: { code: "invalid_session", message: "authentication required." } }, 401),
    );
    render(<FactReview />);

    expect(await screen.findByRole("heading", { name: "You are not signed in" })).toBeVisible();
  });

  it("explains where facts come from when there are none", async () => {
    fetchMock.mockResolvedValue(json({ groups: [] }));
    render(<FactReview />);

    expect(await screen.findByText(/No candidate facts yet/)).toBeVisible();
    expect(screen.getByRole("button", { name: "Apply confirmed facts to profile" })).toBeDisabled();
  });

  it("shows a conflict and confirms a correction, then refreshes the group", async () => {
    fetchMock
      .mockResolvedValueOnce(json(GROUPED))
      .mockResolvedValueOnce(
        json({ fact_id: 1, status: "confirmed", already_set: false }),
      )
      .mockResolvedValueOnce(json(UPDATED));
    const user = userEvent.setup();
    render(<FactReview />);

    const group = await screen.findByRole("region", { name: "Contact email" });
    expect(within(group).getByText("Conflict")).toBeVisible();
    expect(within(group).getByText(/Two documents propose different values/)).toBeVisible();

    // The row controls carry the extracted value in their accessible name, so a
    // screen reader (and this test) can pick the right candidate without guessing.
    await user.type(
      within(group).getByLabelText("Correction for ada@example.edu (optional)"),
      "ada@new.example.edu",
    );
    await user.click(
      within(group).getByRole("button", { name: "Confirm ada@example.edu" }),
    );

    expect(await screen.findByText(/Fact confirmed with your correction/)).toBeVisible();
    expect(await screen.findByText("ada@new.example.edu")).toBeVisible();
    expect(screen.queryByText("Conflict")).toBeNull();
    const patchCall = fetchMock.mock.calls.find(
      ([, init]) => (init as RequestInit | undefined)?.method === "PATCH",
    );
    expect(JSON.parse(String((patchCall?.[1] as RequestInit).body))).toEqual({
      status: "confirmed",
      corrected_value: "ada@new.example.edu",
    });
  });

  it("rejects one candidate without sending a correction", async () => {
    fetchMock
      .mockResolvedValueOnce(json(GROUPED))
      .mockResolvedValueOnce(json({ fact_id: 2, status: "rejected", already_set: false }))
      .mockResolvedValueOnce(json({ groups: [] }));
    const user = userEvent.setup();
    render(<FactReview />);

    const group = await screen.findByRole("region", { name: "Contact email" });
    await user.click(
      within(group).getByRole("button", { name: "Reject ada@old.example.edu" }),
    );

    expect(await screen.findByText(/Fact rejected/)).toBeVisible();
    const patchCall = fetchMock.mock.calls.find(
      ([, init]) => (init as RequestInit | undefined)?.method === "PATCH",
    );
    expect(JSON.parse(String((patchCall?.[1] as RequestInit).body))).toEqual({
      status: "rejected",
    });
  });

  it("applies confirmed facts with explicit overwrite", async () => {
    fetchMock
      .mockResolvedValueOnce(json(GROUPED))
      .mockResolvedValueOnce(json({ applied: ["gpa", "gpa_scale"] }));
    const user = userEvent.setup();
    render(<FactReview />);

    await screen.findByRole("region", { name: "Contact email" });
    await user.click(screen.getByLabelText(/Overwrite profile values/));
    await user.click(screen.getByRole("button", { name: "Apply confirmed facts to profile" }));

    expect(await screen.findByText(/Applied to your profile: CGPA \/ GPA, Grading scale/)).toBeVisible();
    const applyCall = fetchMock.mock.calls.find(([input]) =>
      String(input).endsWith("/api/facts/apply"),
    );
    expect(JSON.parse(String((applyCall?.[1] as RequestInit).body))).toEqual({
      fields: null,
      overwrite: true,
    });
  });

  it("tells the student to save a profile before applying facts", async () => {
    fetchMock
      .mockResolvedValueOnce(json(GROUPED))
      .mockResolvedValueOnce(
        json({ error: { code: "profile_missing", message: "profile not found." } }, 404),
      );
    const user = userEvent.setup();
    render(<FactReview />);

    await screen.findByRole("region", { name: "Contact email" });
    await user.click(screen.getByRole("button", { name: "Apply confirmed facts to profile" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/No profile is saved yet/);
  });

  it("offers a retry when facts cannot be loaded", async () => {
    fetchMock
      .mockResolvedValueOnce(new Response(null, { status: 503 }))
      .mockResolvedValueOnce(json(GROUPED));
    const user = userEvent.setup();
    render(<FactReview />);

    expect(await screen.findByRole("alert")).toHaveTextContent(/status 503/i);
    await user.click(screen.getByRole("button", { name: "Try again" }));

    expect(await screen.findByRole("region", { name: "Contact email" })).toBeVisible();
  });

  it("names the source document, never the internal id", async () => {
    fetchMock.mockResolvedValue(json(GROUPED));
    render(<FactReview />);

    const group = await screen.findByRole("region", { name: "Contact email" });
    expect(within(group).getByText(/From cv\.pdf/)).toBeVisible();
    expect(within(group).getByText(/From old-cv\.pdf/)).toBeVisible();
    expect(within(group).queryByText(/document:1/)).toBeNull();
  });

  it("says so when a confirmed fact outlived its document", async () => {
    fetchMock.mockResolvedValue(
      json({
        groups: [
          {
            field: "phone",
            has_conflict: false,
            confirmed: [
              {
                id: 4,
                field: "phone",
                value: "+91 98765 43210",
                source: "document:9",
                source_name: null,
                status: "confirmed",
                confidence: 0.7,
              },
            ],
            pending: [],
          },
        ],
      }),
    );
    render(<FactReview />);

    const group = await screen.findByRole("region", { name: "Phone" });
    expect(within(group).getByText(/From a document you deleted/)).toBeVisible();
  });

  it("corrects a confirmed fact in place", async () => {
    const CORRECTED = {
      groups: [
        GROUPED.groups[0],
        {
          field: "gpa",
          has_conflict: false,
          confirmed: [
            {
              id: 3,
              field: "gpa",
              value: "3.9/4.0",
              source: "document:1",
              source_name: "cv.pdf",
              status: "confirmed",
              confidence: 0.8,
            },
          ],
          pending: [],
        },
      ],
    };
    fetchMock
      .mockResolvedValueOnce(json(GROUPED))
      .mockResolvedValueOnce(json({ fact_id: 3, status: "confirmed", already_set: true }))
      .mockResolvedValueOnce(json(CORRECTED));
    const user = userEvent.setup();
    render(<FactReview />);

    const group = await screen.findByRole("region", { name: "CGPA / GPA" });
    await user.click(within(group).getByRole("button", { name: "Correct 3.8/4.0" }));
    const field = within(group).getByLabelText("Corrected value for 3.8/4.0");
    await user.clear(field);
    await user.type(field, "3.9/4.0");
    await user.click(
      within(group).getByRole("button", { name: "Save correction to 3.8/4.0" }),
    );

    // `already_set` is true because the row was already confirmed, but the value
    // changed: the message must not claim nothing happened.
    expect(await screen.findByText(/Fact corrected/)).toBeVisible();
    expect(await screen.findByText("3.9/4.0")).toBeVisible();
    const patchCall = fetchMock.mock.calls.find(
      ([, init]) => (init as RequestInit | undefined)?.method === "PATCH",
    );
    expect(JSON.parse(String((patchCall?.[1] as RequestInit).body))).toEqual({
      status: "confirmed",
      corrected_value: "3.9/4.0",
    });
  });

  it("keeps apply disabled when only non-applicable facts are confirmed", async () => {
    fetchMock.mockResolvedValue(
      json({
        groups: [
          {
            field: "expected_graduation_year",
            has_conflict: false,
            confirmed: [
              {
                id: 5,
                field: "expected_graduation_year",
                value: "2027",
                source: "document:1",
                source_name: "cv.pdf",
                status: "confirmed",
                confidence: 0.6,
              },
            ],
            pending: [],
          },
        ],
      }),
    );
    render(<FactReview />);

    await screen.findByRole("region", { name: "Expected graduation year" });
    expect(screen.getByRole("button", { name: "Apply confirmed facts to profile" })).toBeDisabled();
  });

  it("returns to the signed-out panel when a decision loses the session", async () => {
    fetchMock
      .mockResolvedValueOnce(json(GROUPED))
      .mockResolvedValueOnce(
        json({ error: { code: "invalid_session", message: "authentication required." } }, 401),
      );
    const user = userEvent.setup();
    render(<FactReview />);

    const group = await screen.findByRole("region", { name: "Contact email" });
    await user.click(
      within(group).getByRole("button", { name: "Reject ada@old.example.edu" }),
    );

    expect(await screen.findByRole("heading", { name: "You are not signed in" })).toBeVisible();
  });
});
