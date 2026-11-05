// @vitest-environment jsdom
import "@testing-library/jest-dom/vitest";

import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { renderToString } from "react-dom/server";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { DocumentManager } from "./document-manager";

const fetchMock = vi.fn();

const CV = {
  id: 7,
  kind: "cv",
  display_name: "ada-cv.pdf",
  media_type: "application/pdf",
  size_bytes: 2048,
  status: "stored",
  sha256: "a".repeat(64),
  uploaded_at: "2026-11-02T10:00:00Z",
  parsed_at: null,
  parse_error: null,
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
  vi.restoreAllMocks();
});

describe("DocumentManager", () => {
  it("asks the visitor to sign in without a session", async () => {
    fetchMock.mockResolvedValue(
      json({ error: { code: "invalid_session", message: "authentication required." } }, 401),
    );
    render(<DocumentManager />);

    expect(await screen.findByRole("heading", { name: "You are not signed in" })).toBeVisible();
  });

  it("shows an empty list and still offers the upload form", async () => {
    fetchMock.mockResolvedValue(json({ documents: [] }));
    render(<DocumentManager />);

    expect(await screen.findByText("No document has been uploaded yet.")).toBeVisible();
    expect(screen.getByLabelText("File")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Upload document" })).toBeEnabled();
  });

  it("uploads multipart data and shows the returned document", async () => {
    fetchMock.mockImplementation((input: RequestInfo | URL, init?: RequestInit) => {
      if (String(input).endsWith("/api/documents") && init?.method === "POST") {
        return Promise.resolve(json({ ...CV, id: 9, display_name: "new-cv.pdf" }, 201));
      }
      return Promise.resolve(json({ documents: [] }));
    });
    const user = userEvent.setup();
    render(<DocumentManager />);

    await screen.findByText("No document has been uploaded yet.");
    const file = new File(["%PDF-1.4"], "new-cv.pdf", { type: "application/pdf" });
    await user.upload(screen.getByLabelText("File"), file);
    await user.selectOptions(screen.getByLabelText("What is it?"), "transcript");
    await user.click(screen.getByRole("button", { name: "Upload document" }));

    expect(await screen.findByRole("heading", { name: "new-cv.pdf" })).toBeVisible();
    expect(screen.getByText(/uploaded\. Extract facts next/)).toBeVisible();
    const uploadCall = fetchMock.mock.calls.find(
      ([input, init]) =>
        String(input).endsWith("/api/documents") &&
        (init as RequestInit | undefined)?.method === "POST",
    );
    const form = (uploadCall?.[1] as RequestInit).body as FormData;
    expect(form.get("kind")).toBe("transcript");
    expect((form.get("file") as File).name).toBe("new-cv.pdf");
  });

  it("extracts facts and reports how many candidates arrived", async () => {
    fetchMock
      .mockResolvedValueOnce(json({ documents: [CV] }))
      .mockResolvedValueOnce(
        json({
          document: { ...CV, status: "parsed", parsed_at: "2026-11-03T08:00:00Z" },
          character_count: 1200,
          fact_count: 3,
        }),
      );
    const user = userEvent.setup();
    render(<DocumentManager />);

    await screen.findByRole("heading", { name: "ada-cv.pdf" });
    await user.click(screen.getByRole("button", { name: "Extract facts from ada-cv.pdf" }));

    expect(await screen.findByText(/found 3 candidate facts/)).toBeVisible();
    expect(screen.getByText(/Parsed on 2026-11-03/)).toBeVisible();
    expect(
      screen.getByRole("button", { name: "Extract facts again from ada-cv.pdf" }),
    ).toBeInTheDocument();
  });

  it("keeps a failed parse visible with the server's reason", async () => {
    fetchMock
      .mockResolvedValueOnce(json({ documents: [CV] }))
      .mockResolvedValueOnce(
        json(
          {
            error: {
              code: "document_unreadable",
              message: "the document has no extractable text.",
            },
          },
          422,
        ),
      )
      .mockResolvedValueOnce(
        json({
          documents: [
            {
              ...CV,
              status: "failed",
              parse_error: "no extractable text (scanned or encrypted PDF?)",
            },
          ],
        }),
      );
    const user = userEvent.setup();
    render(<DocumentManager />);

    await screen.findByRole("heading", { name: "ada-cv.pdf" });
    await user.click(screen.getByRole("button", { name: "Extract facts from ada-cv.pdf" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/OCR/);
    expect(await screen.findByText(/Parsing failed/)).toBeVisible();
    expect(screen.getByText(/scanned or encrypted/)).toBeVisible();
  });

  it("deletes a document after confirmation", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    fetchMock
      .mockResolvedValueOnce(json({ documents: [CV] }))
      .mockResolvedValueOnce(new Response(null, { status: 204 }));
    const user = userEvent.setup();
    render(<DocumentManager />);

    await screen.findByRole("heading", { name: "ada-cv.pdf" });
    await user.click(screen.getByRole("button", { name: "Delete ada-cv.pdf" }));

    expect(await screen.findByText(/ada-cv\.pdf deleted/)).toBeVisible();
    expect(screen.queryByRole("heading", { name: "ada-cv.pdf" })).toBeNull();
    expect(window.confirm).toHaveBeenCalledWith(expect.stringMatching(/Facts you already confirmed/));
  });

  it("does not delete when the confirmation is cancelled", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(false);
    fetchMock.mockResolvedValueOnce(json({ documents: [CV] }));
    const user = userEvent.setup();
    render(<DocumentManager />);

    await screen.findByRole("heading", { name: "ada-cv.pdf" });
    await user.click(screen.getByRole("button", { name: "Delete ada-cv.pdf" }));

    await waitFor(() => expect(window.confirm).toHaveBeenCalled());
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(screen.getByRole("heading", { name: "ada-cv.pdf" })).toBeVisible();
  });

  it("shows a locked-file deletion failure and keeps the row", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    fetchMock
      .mockResolvedValueOnce(json({ documents: [CV] }))
      .mockResolvedValueOnce(
        json(
          {
            error: {
              code: "deletion_incomplete",
              message: "could not remove the stored file.",
            },
          },
          409,
        ),
      )
      .mockResolvedValueOnce(json({ documents: [CV] }));
    const user = userEvent.setup();
    render(<DocumentManager />);

    await screen.findByRole("heading", { name: "ada-cv.pdf" });
    await user.click(screen.getByRole("button", { name: "Delete ada-cv.pdf" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(/in use/);
    expect(screen.getByRole("heading", { name: "ada-cv.pdf" })).toBeVisible();
  });

  it("describes a deduped re-upload as the existing document", async () => {
    const parsed = {
      ...CV,
      status: "parsed",
      parsed_at: "2026-11-03T08:00:00Z",
    };
    fetchMock
      .mockResolvedValueOnce(json({ documents: [parsed] }))
      .mockResolvedValueOnce(json(parsed, 201));
    const user = userEvent.setup();
    render(<DocumentManager />);

    await screen.findByRole("heading", { name: "ada-cv.pdf" });
    const file = new File(["%PDF-1.4"], "ada-cv.pdf", { type: "application/pdf" });
    await user.upload(screen.getByLabelText("File"), file);
    await user.click(screen.getByRole("button", { name: "Upload document" }));

    // Identical bytes return the stored row, which is already parsed: the notice
    // must not tell the student to extract facts that already exist.
    expect(await screen.findByText(/already uploaded and has been parsed/)).toBeVisible();
    expect(screen.getAllByRole("heading", { name: "ada-cv.pdf" })).toHaveLength(1);
  });

  it("returns to the signed-out panel when an action loses the session", async () => {
    vi.spyOn(window, "confirm").mockReturnValue(true);
    fetchMock
      .mockResolvedValueOnce(json({ documents: [CV] }))
      .mockResolvedValueOnce(
        json({ error: { code: "invalid_session", message: "authentication required." } }, 401),
      );
    const user = userEvent.setup();
    render(<DocumentManager />);

    await screen.findByRole("heading", { name: "ada-cv.pdf" });
    await user.click(screen.getByRole("button", { name: "Delete ada-cv.pdf" }));

    expect(await screen.findByRole("heading", { name: "You are not signed in" })).toBeVisible();
  });

  it("cannot submit natively before hydration", () => {
    // The upload form appears only after the document list resolves, so the
    // server output is the loading shell: nothing to submit before hydration.
    const html = renderToString(<DocumentManager />);

    expect(html).toContain("Loading your documents");
    expect(html).not.toContain("<form");
    expect(html).not.toContain("<button");
    expect(html).not.toMatch(/method="get"/i);
  });
});
