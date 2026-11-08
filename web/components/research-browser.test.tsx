// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";

import { cleanup, render, screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ResearchBrowser } from "./research-browser";

const fetchMock = vi.fn();

const TUM = {
  id: 1,
  name: "Technical University of Munich",
  country: "Germany",
  website_url: "https://www.tum.de",
  program_count: 2,
  department_count: 1,
  last_checked_at: "2026-11-05T12:00:00Z",
  stale: false,
};

const DELFT = {
  id: 2,
  name: "Delft University of Technology",
  country: "Netherlands",
  website_url: null,
  program_count: 1,
  department_count: 1,
  last_checked_at: "2026-11-04T09:30:00Z",
  stale: false,
};

const PHYSICS = {
  id: 5,
  institution_id: 1,
  name: "Physics",
  website_url: null,
  program_count: 2,
  last_checked_at: "2026-11-05T12:00:00Z",
  stale: false,
};

const PROGRAM = {
  id: 101,
  name: "MSc Research Internship",
  institution: "Technical University of Munich",
  institution_id: 1,
  department: "Physics",
  department_id: 5,
  country: "Germany",
  opportunity_type: "research internship",
  degree_level: "master",
  research_areas: ["machine learning", "physics"],
  official_url: "https://www.tum.de/en/studies/programs/msc-research-internship",
  evidence_urls: ["https://www.tum.de/en/studies/programs/msc-research-internship"],
  eligibility: ["Enrolled in a master's programme"],
  application_method: "Apply through the TUM portal.",
  opening_date: "2026-12-01",
  closing_date: "2027-01-15",
  start_date: null,
  duration: "6 months",
  stipend: null,
  tuition: null,
  accommodation: null,
  required_documents: ["CV", "Transcript"],
  status: "upcoming",
  confidence: 0.8,
  last_checked_at: "2026-11-05T12:00:00Z",
  stale: false,
};

const STALE_PROGRAM = {
  ...PROGRAM,
  id: 102,
  name: "BSc Physics",
  degree_level: "bachelor",
  official_url: "https://www.tum.de/en/studies/programs/bsc-physics",
  evidence_urls: ["https://www.tum.de/en/studies/programs/bsc-physics"],
  last_checked_at: "2026-09-01T08:00:00Z",
  stale: true,
};

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "content-type": "application/json" },
  });
}

function institutionPage(institutions: readonly unknown[], total = institutions.length) {
  return json({ institutions, total, limit: 10, offset: 0 });
}

function departmentPage(institution: unknown, departments: readonly unknown[], total = departments.length) {
  return json({ institution, departments, total, limit: 10, offset: 0 });
}

function programPage(programs: readonly unknown[], total = programs.length) {
  return json({ programs, total, limit: 10, offset: 0 });
}

function defaultRouter(
  overrides: {
    readonly institutions?: () => Response;
    readonly programs?: () => Response;
    readonly departments?: () => Response;
    readonly detail?: () => Response;
  } = {},
) {
  return (input: RequestInfo | URL): Promise<Response> => {
    const url = String(input);
    if (/\/api\/research\/institutions\/\d+\/departments/.test(url)) {
      return Promise.resolve(
        overrides.departments?.() ?? departmentPage(TUM, [PHYSICS]),
      );
    }
    if (/\/api\/research\/programs\/\d+$/.test(url)) {
      return Promise.resolve(
        overrides.detail?.() ?? json({ ...PROGRAM, stipend: "EUR 1,200 per month" }),
      );
    }
    if (url.includes("/api/research/programs")) {
      return Promise.resolve(
        overrides.programs?.() ?? programPage([PROGRAM, STALE_PROGRAM]),
      );
    }
    if (url.includes("/api/research/institutions")) {
      return Promise.resolve(
        overrides.institutions?.() ?? institutionPage([TUM, DELFT]),
      );
    }
    return Promise.resolve(json({ error: { code: "not_found", message: "no route" } }, 404));
  };
}

function requestedUrls(): string[] {
  return fetchMock.mock.calls.map(([input]) => String(input));
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

describe("ResearchBrowser", () => {
  it("asks the visitor to sign in without a session", async () => {
    fetchMock.mockResolvedValue(
      json({ error: { code: "invalid_session", message: "authentication required." } }, 401),
    );
    render(<ResearchBrowser />);

    expect(await screen.findByRole("heading", { name: "You are not signed in" })).toBeVisible();
  });

  it("lists cached institutions and programs with their source and checked date", async () => {
    fetchMock.mockImplementation(defaultRouter());
    render(<ResearchBrowser />);

    expect(
      await screen.findByRole("heading", { name: "MSc Research Internship" }),
    ).toBeVisible();
    expect(screen.getByRole("heading", { name: "Technical University of Munich" })).toBeVisible();
    expect(screen.getAllByText("Cached").length).toBeGreaterThan(0);
    expect(screen.getByText("Stale", { selector: ".badge" })).toBeVisible();
    expect(screen.getAllByText(/Last checked 2026-11-05/).length).toBeGreaterThan(0);
    expect(
      screen.getAllByRole("link", { name: "Official page" })[0],
    ).toHaveAttribute("href", PROGRAM.official_url);
    expect(screen.getByText(/2 matches/)).toBeVisible();
    expect(screen.getByText(/2 programs · cache only/)).toBeVisible();
  });

  it("drills into an institution and scopes programs to it", async () => {
    fetchMock.mockImplementation(defaultRouter());
    const user = userEvent.setup();
    render(<ResearchBrowser />);

    await screen.findByRole("heading", { name: "MSc Research Internship" });
    await user.click(screen.getByRole("button", { name: "Browse Technical University of Munich" }));

    expect(await screen.findByRole("heading", { name: "Physics" })).toBeVisible();
    expect(
      screen.getByRole("heading", { name: "Programs at Technical University of Munich" }),
    ).toBeVisible();
    expect(requestedUrls()).toContainEqual(
      expect.stringContaining("/api/research/institutions/1/departments"),
    );
    expect(requestedUrls()).toContainEqual(expect.stringContaining("institution_id=1"));
  });

  it("filters programs by the selected department", async () => {
    fetchMock.mockImplementation(
      defaultRouter({
        departments: () => departmentPage(TUM, [PHYSICS]),
        programs: () =>
          requestedUrls().some((url) => url.includes("department_id=5"))
            ? programPage([PROGRAM])
            : programPage([PROGRAM, STALE_PROGRAM]),
      }),
    );
    const user = userEvent.setup();
    render(<ResearchBrowser />);

    await screen.findByRole("heading", { name: "MSc Research Internship" });
    await user.click(screen.getByRole("button", { name: "Browse Technical University of Munich" }));
    await screen.findByRole("heading", { name: "Physics" });
    await user.click(screen.getByRole("button", { name: "Filter programs by Physics" }));

    await waitFor(() =>
      expect(requestedUrls()).toContainEqual(expect.stringContaining("department_id=5")),
    );
    expect(
      screen.getByRole("heading", { name: "Programs in Physics" }),
    ).toBeVisible();
  });

  it("sends country, degree and area filters and resets the drill-down", async () => {
    fetchMock.mockImplementation(defaultRouter());
    const user = userEvent.setup();
    render(<ResearchBrowser />);

    await screen.findByRole("heading", { name: "MSc Research Internship" });
    await user.click(screen.getByRole("button", { name: "Browse Technical University of Munich" }));
    await screen.findByRole("heading", { name: "Physics" });

    await user.type(screen.getByLabelText("Country"), "Netherlands");
    await user.selectOptions(screen.getByLabelText("Degree level"), "master");
    await user.type(screen.getByLabelText("Research area"), "data science");
    await user.click(screen.getByRole("button", { name: "Apply filters" }));

    await waitFor(() =>
      expect(requestedUrls()).toContainEqual(expect.stringContaining("country=Netherlands")),
    );
    const programCall = requestedUrls().find(
      (url) =>
        url.includes("/api/research/programs?") &&
        url.includes("degree_level=master") &&
        url.includes("area=data+science"),
    );
    expect(programCall).toBeDefined();
    expect(programCall).not.toContain("institution_id=");
    expect(screen.getByRole("heading", { name: "Programs in the shared cache" })).toBeVisible();
  });

  it("loads a program's latest cached copy when details are opened", async () => {
    fetchMock.mockImplementation(defaultRouter());
    const user = userEvent.setup();
    render(<ResearchBrowser />);

    await screen.findByRole("heading", { name: "MSc Research Internship" });
    // The list row has no stipend; the detail endpoint returns the stored one.
    expect(screen.queryByText("EUR 1,200 per month")).not.toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Details for MSc Research Internship" }));

    expect(await screen.findByText("EUR 1,200 per month")).toBeVisible();
    expect(requestedUrls()).toContainEqual(
      expect.stringMatching(/\/api\/research\/programs\/101$/),
    );
    const detail = screen.getByRole("button", {
      name: "Details for MSc Research Internship",
    }).parentElement;
    expect(within(detail as HTMLElement).getAllByText("Not stated").length).toBeGreaterThan(0);
  });

  it("explains an empty cache instead of inventing a result", async () => {
    fetchMock.mockImplementation(
      defaultRouter({
        institutions: () => institutionPage([], 0),
        programs: () => programPage([], 0),
      }),
    );
    render(<ResearchBrowser />);

    expect(
      await screen.findByText(/No institution matches these filters/),
    ).toBeVisible();
    expect(screen.getByText(/No cached program matches these filters/)).toBeVisible();
  });

  it("pages institutions with an offset instead of refetching the first page", async () => {
    let institutionCall = 0;
    fetchMock.mockImplementation((input: RequestInfo | URL) => {
      const url = String(input);
      if (url.includes("/api/research/institutions")) {
        institutionCall += 1;
        return Promise.resolve(
          url.includes("offset=1")
            ? institutionPage([DELFT], 2)
            : institutionPage([TUM], 2),
        );
      }
      return Promise.resolve(programPage([PROGRAM]));
    });
    const user = userEvent.setup();
    render(<ResearchBrowser />);

    await screen.findByRole("heading", { name: "Technical University of Munich" });
    expect(screen.queryByRole("heading", { name: "Delft University of Technology" })).toBeNull();
    await user.click(screen.getByRole("button", { name: "Load more institutions" }));

    expect(
      await screen.findByRole("heading", { name: "Delft University of Technology" }),
    ).toBeVisible();
    expect(institutionCall).toBeGreaterThanOrEqual(2);
    expect(requestedUrls()).toContainEqual(expect.stringContaining("offset=1"));
  });

  it("reports a failed cache read with a retry action", async () => {
    fetchMock.mockImplementation(() =>
      Promise.resolve(
        json({ error: { code: "http_error", message: "the cache is unavailable." } }, 500),
      ),
    );
    render(<ResearchBrowser />);

    expect(await screen.findByText("the cache is unavailable.")).toBeVisible();
    expect(screen.getByRole("button", { name: "Try again" })).toBeVisible();
  });
});
