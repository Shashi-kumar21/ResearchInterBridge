// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { FacultyList, facultyScopeKey } from "./faculty-list";

const fetchMock = vi.fn();

const PAPER = {
  id: 7,
  title: "Graph Learning",
  year: 2025,
  doi: "10.1234/g",
  url: "https://doi.org/10.1234/g",
  venue: "NeurIPS",
  authors: ["Jane Doe", "Ravi Kumar"],
  recent: true,
  last_checked_at: "2026-11-05T12:00:00Z",
  stale: false,
};

const JANE = {
  id: 1,
  full_name: "Jane Doe",
  title: "Professor",
  institution: "Example University",
  institution_id: 3,
  department: "Computer Science",
  department_id: 9,
  country: "Germany",
  lab_name: null,
  profile_url: "https://www.cs.example.edu/people/jane.doe",
  evidence_urls: ["https://www.cs.example.edu/people/jane.doe"],
  research_interests: ["machine learning"],
  email: "jane.doe@cs.example.edu",
  email_status: "verified",
  email_source_url: "https://www.cs.example.edu/people/jane.doe",
  contact_sendable: true,
  orcid: "0000-0002-1825-0097",
  recruitment_evidence_url: null,
  highlighted_publications: [PAPER],
  publication_count: 4,
  confidence: 0.9,
  last_checked_at: "2026-11-05T12:00:00Z",
  stale: false,
};

const RAVI = {
  ...JANE,
  id: 2,
  full_name: "Ravi Kumar",
  title: null,
  profile_url: "https://www.cs.example.edu/people/ravi",
  email: null,
  email_status: "unverified",
  email_source_url: "https://ravi.example.org/",
  contact_sendable: false,
  orcid: null,
  highlighted_publications: [],
  publication_count: 0,
};

const ADA = {
  ...JANE,
  id: 3,
  full_name: "Ada Byron",
  profile_url: "https://www.cs.example.edu/people/ada",
  email: null,
  email_status: "unavailable",
  email_source_url: null,
  contact_sendable: false,
  highlighted_publications: [],
  publication_count: 0,
};

const STALE_JANE = { ...JANE, stale: true, contact_sendable: false };

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function facultyPage(faculty: readonly unknown[], total = faculty.length) {
  return json({ faculty, total, limit: 10, offset: 0 });
}

function requestedUrls(): string[] {
  return fetchMock.mock.calls.map(([input]) => String(input));
}

function renderList(scope = {}, onSessionLost = vi.fn()) {
  return render(
    <FacultyList
      key={facultyScopeKey(scope)}
      scope={scope}
      title="Faculty in the shared cache"
      onSessionLost={onSessionLost}
    />,
  );
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

describe("FacultyList", () => {
  it("shows a verified address as text with its source, never as a mailto link", async () => {
    fetchMock.mockResolvedValue(facultyPage([JANE]));
    renderList();

    expect(await screen.findByRole("heading", { name: "Jane Doe" })).toBeVisible();
    expect(screen.getByText("jane.doe@cs.example.edu")).toBeVisible();
    expect(screen.getByRole("link", { name: "the official page" })).toHaveAttribute(
      "href",
      JANE.email_source_url,
    );
    expect(document.querySelector('a[href^="mailto:"]')).toBeNull();
    expect(screen.getByRole("link", { name: "Official profile" })).toHaveAttribute(
      "href",
      JANE.profile_url,
    );
    expect(screen.getByText(/4 papers linked by ORCID 0000-0002-1825-0097/)).toBeVisible();
    expect(screen.getByRole("link", { name: "Graph Learning" })).toHaveAttribute("href", PAPER.url);
    expect(screen.getByText(/1 faculty · cache only/)).toBeVisible();
  });

  it("explains an unverified or missing address without showing one", async () => {
    fetchMock.mockResolvedValue(facultyPage([RAVI, ADA]));
    renderList();

    await screen.findByRole("heading", { name: "Ravi Kumar" });
    expect(screen.getByText(/Email not verified/)).toBeVisible();
    expect(screen.getByRole("link", { name: "Where it was found" })).toHaveAttribute(
      "href",
      "https://ravi.example.org/",
    );
    expect(screen.getByText(/No email address is stated on the official page/)).toBeVisible();
    expect(screen.queryByText(/@/)).toBeNull();
  });

  it("marks a stale verified address as orientation only", async () => {
    fetchMock.mockResolvedValue(facultyPage([STALE_JANE]));
    renderList();

    await screen.findByRole("heading", { name: "Jane Doe" });
    expect(screen.getByText("Stale", { selector: ".badge" })).toBeVisible();
    expect(screen.getByText(/address is for orientation only/)).toBeVisible();
  });

  it("sends the scope as query parameters", async () => {
    fetchMock.mockResolvedValue(facultyPage([]));
    renderList({ departmentId: 9, area: "machine learning", country: "Germany" });

    await waitFor(() => expect(requestedUrls()).toHaveLength(1));
    const [url] = requestedUrls();
    expect(url).toContain("/api/research/faculty?");
    expect(url).toContain("department_id=9");
    expect(url).toContain("area=machine+learning");
    expect(url).toContain("country=Germany");
    expect(url).not.toContain("institution_id");
  });

  it("explains an empty level instead of guessing people", async () => {
    fetchMock.mockResolvedValue(facultyPage([]));
    renderList();

    expect(await screen.findByText(/No cached faculty match this level/)).toBeVisible();
  });

  it("loads every attributed paper with abstracts when details are opened", async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (/\/api\/research\/faculty\/1$/.test(url)) {
        return Promise.resolve(
          json({
            ...JANE,
            publications: [
              { ...PAPER, abstract: "We learn on graphs." },
              { ...PAPER, id: 8, title: "Older Work", year: 2015, recent: false, authors: [], abstract: null },
            ],
          }),
        );
      }
      return Promise.resolve(facultyPage([JANE]));
    });
    const user = userEvent.setup();
    renderList();

    await screen.findByRole("heading", { name: "Jane Doe" });
    await user.click(screen.getByRole("button", { name: "Details for Jane Doe" }));

    expect(await screen.findByText("All attributed papers (2)")).toBeVisible();
    expect(screen.getByRole("link", { name: "Older Work" })).toBeVisible();
    expect(screen.getByText("We learn on graphs.")).toBeInTheDocument();
    expect(screen.getByText(/Author list not stored/)).toBeVisible();
  });

  it("pages with an offset", async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL) =>
      Promise.resolve(
        String(input).includes("offset=1") ? facultyPage([RAVI], 2) : facultyPage([JANE], 2),
      ),
    );
    const user = userEvent.setup();
    renderList();

    await screen.findByRole("heading", { name: "Jane Doe" });
    await user.click(screen.getByRole("button", { name: "Load more faculty" }));

    expect(await screen.findByRole("heading", { name: "Ravi Kumar" })).toBeVisible();
    expect(screen.queryByRole("button", { name: "Load more faculty" })).toBeNull();
  });

  it("reports a lost session to the parent", async () => {
    fetchMock.mockResolvedValue(
      json({ error: { code: "invalid_session", message: "authentication required." } }, 401),
    );
    const onSessionLost = vi.fn();
    renderList({}, onSessionLost);

    await waitFor(() => expect(onSessionLost).toHaveBeenCalled());
  });

  it("shows a failed read inline", async () => {
    fetchMock.mockResolvedValue(
      json({ error: { code: "http_error", message: "the cache is unavailable." } }, 500),
    );
    renderList();

    expect(await screen.findByText("the cache is unavailable.")).toBeVisible();
  });
});
