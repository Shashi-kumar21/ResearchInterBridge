"use client";

// Plain anchors are deliberate in this client component: they keep the screen
// renderable in tests without the App Router.

import { useEffect, useRef, useState, type FormEvent } from "react";

import {
  ApiError,
  deleteDocument,
  listDocuments,
  parseDocument,
  uploadDocument,
  type DocumentKind,
  type DocumentRecord,
} from "@/lib/api";
import { describeApiError } from "@/lib/errors";
import { formatBytes, formatDate } from "@/lib/format";
import { useHydrated } from "@/lib/hydration";

import { LoadingPanel, RetryPanel, SignedOutPanel } from "./account-panels";

type LoadState = "loading" | "ready" | "signed-out" | "failed";

const KIND_LABELS: Record<string, string> = {
  cv: "CV",
  transcript: "Transcript",
  other: "Other document",
};

function kindLabel(kind: string): string {
  return KIND_LABELS[kind] ?? kind;
}

function statusLabel(status: string): string {
  switch (status) {
    case "stored":
      return "Uploaded, not parsed yet";
    case "parsed":
      return "Parsed";
    case "failed":
      return "Parsing failed";
    default:
      return status;
  }
}

function isSessionLost(failure: unknown): boolean {
  return failure instanceof ApiError && failure.status === 401;
}

function plural(count: number, singular: string, pluralForm: string): string {
  return count === 1 ? singular : pluralForm;
}

/**
 * Document upload, list, parse and delete.
 *
 * The list is server state: uploads are prepended from the API response, and a
 * failed parse refreshes the list because the server persists the failure and
 * its reason (`status=failed`, `parse_error`) on the document row.
 */
export function DocumentManager() {
  const hydrated = useHydrated();
  const [state, setState] = useState<LoadState>("loading");
  const [documents, setDocuments] = useState<readonly DocumentRecord[]>([]);
  const [file, setFile] = useState<File | null>(null);
  const [kind, setKind] = useState<DocumentKind>("cv");
  const [uploading, setUploading] = useState(false);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [reloadToken, setReloadToken] = useState(0);
  const fileInput = useRef<HTMLInputElement>(null);

  useEffect(() => {
    let cancelled = false;
    listDocuments()
      .then((list) => {
        if (cancelled) {
          return;
        }
        setDocuments(list.documents);
        setState("ready");
      })
      .catch((failure: unknown) => {
        if (cancelled) {
          return;
        }
        if (isSessionLost(failure)) {
          setState("signed-out");
          return;
        }
        setError(describeApiError(failure));
        setState("failed");
      });
    return () => {
      cancelled = true;
    };
  }, [reloadToken]);

  function retry(): void {
    setError(null);
    setNotice(null);
    setDocuments([]);
    setState("loading");
    setReloadToken((token) => token + 1);
  }

  async function refreshList(): Promise<void> {
    try {
      const list = await listDocuments();
      setDocuments(list.documents);
    } catch (failure) {
      if (isSessionLost(failure)) {
        setState("signed-out");
        return;
      }
      setError(describeApiError(failure));
    }
  }

  async function onUpload(event: FormEvent<HTMLFormElement>): Promise<void> {
    event.preventDefault();
    if (!hydrated) {
      return;
    }
    setError(null);
    setNotice(null);
    if (file === null) {
      setError("Choose a PDF, DOCX or TXT file first.");
      return;
    }
    setUploading(true);
    try {
      const uploaded = await uploadDocument(file, kind);
      // Identical bytes dedupe on the server and return the stored row, which
      // may already be parsed: say so instead of claiming a new upload.
      const existing = documents.find((document) => document.id === uploaded.id);
      setDocuments((current) => [
        uploaded,
        ...current.filter((document) => document.id !== uploaded.id),
      ]);
      setNotice(
        existing === undefined
          ? `${uploaded.display_name} uploaded. Extract facts next, or upload another document.`
          : existing.status === "parsed"
            ? `${uploaded.display_name} was already uploaded and has been parsed. Its facts are on the fact-review page.`
            : `${uploaded.display_name} was already uploaded. Choose “Extract facts” on its row.`,
      );
      setFile(null);
      if (fileInput.current) {
        fileInput.current.value = "";
      }
    } catch (failure) {
      if (isSessionLost(failure)) {
        setState("signed-out");
      } else {
        setError(describeApiError(failure));
      }
    } finally {
      setUploading(false);
    }
  }

  async function onParse(document: DocumentRecord): Promise<void> {
    setBusyId(document.id);
    setError(null);
    setNotice(null);
    try {
      const result = await parseDocument(document.id);
      setDocuments((current) =>
        current.map((item) => (item.id === result.document.id ? result.document : item)),
      );
      setNotice(
        result.fact_count === 0
          ? `Read ${result.character_count} characters from ${document.display_name}, but found no facts to confirm.`
          : `Read ${result.character_count} characters from ${document.display_name} and found ${result.fact_count} candidate ${plural(result.fact_count, "fact", "facts")}. Review them before they can be used.`,
      );
    } catch (failure) {
      if (isSessionLost(failure)) {
        setState("signed-out");
      } else {
        setError(describeApiError(failure));
        // A failed parse is persisted with its reason; show that row state.
        await refreshList();
      }
    } finally {
      setBusyId(null);
    }
  }

  async function onDelete(document: DocumentRecord): Promise<void> {
    const confirmed = window.confirm(
      `Delete ${document.display_name}? Facts you already confirmed are kept.`,
    );
    if (!confirmed) {
      return;
    }
    setBusyId(document.id);
    setError(null);
    setNotice(null);
    try {
      await deleteDocument(document.id);
      setDocuments((current) => current.filter((item) => item.id !== document.id));
      setNotice(`${document.display_name} deleted. Confirmed facts are kept.`);
    } catch (failure) {
      if (isSessionLost(failure)) {
        setState("signed-out");
      } else {
        setError(describeApiError(failure));
        await refreshList();
      }
    } finally {
      setBusyId(null);
    }
  }

  if (state === "loading") {
    return <LoadingPanel message="Loading your documents…" />;
  }

  if (state === "signed-out") {
    return <SignedOutPanel intro="Documents belong to a signed-in account." />;
  }

  if (state === "failed") {
    return (
      <RetryPanel message={error ?? "Your documents could not be loaded."} onRetry={retry} />
    );
  }

  return (
    <div className="panel">
      {error ? (
        <p className="alert" role="alert">
          {error}
        </p>
      ) : null}
      {notice ? (
        <p className="alert alert-ok" role="status">
          {notice}
        </p>
      ) : null}

      <form method="post" onSubmit={onUpload}>
        <h2>Upload a document</h2>
        <p className="hint">
          PDF, DOCX or TXT, up to 10 MB. Upload needs the document-processing consent; the
          extracted text stays on this instance.
        </p>
        <div className="field-row">
          <div className="field">
            <label htmlFor="document-file">File</label>
            <input
              ref={fileInput}
              id="document-file"
              name="file"
              type="file"
              accept=".pdf,.docx,.txt,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document,text/plain"
              aria-describedby="document-file-hint"
              onChange={(event) => setFile(event.target.files?.[0] ?? null)}
            />
            <p className="hint" id="document-file-hint">
              A scan or a password-protected PDF can be stored, but its text cannot be read
              without OCR.
            </p>
          </div>
          <div className="field">
            <label htmlFor="document-kind">What is it?</label>
            <select
              id="document-kind"
              name="kind"
              value={kind}
              onChange={(event) => setKind(event.target.value as DocumentKind)}
            >
              <option value="cv">CV</option>
              <option value="transcript">Transcript</option>
              <option value="other">Other document</option>
            </select>
          </div>
        </div>
        <div className="button-row">
          <button className="button" type="submit" disabled={!hydrated || uploading}>
            {uploading ? "Uploading…" : "Upload document"}
          </button>
          <a href="/onboarding">Edit your profile</a>
        </div>
      </form>

      <div className="list-heading">
        <h2>Your documents</h2>
        <a href="/facts">Review extracted facts</a>
      </div>

      {documents.length === 0 ? (
        <p className="hint">No document has been uploaded yet.</p>
      ) : (
        <ul className="status-list" role="list">
          {documents.map((document) => (
            <li key={document.id}>
              <div>
                <h3>{document.display_name}</h3>
                <p>
                  {kindLabel(document.kind)} · {formatBytes(document.size_bytes)} ·{" "}
                  {document.media_type} · uploaded {formatDate(document.uploaded_at)}
                </p>
                <p className="hint">
                  {statusLabel(document.status)}
                  {document.parsed_at
                    ? ` on ${formatDate(document.parsed_at)}`
                    : ""}
                  {document.parse_error ? ` — ${document.parse_error}` : ""}
                </p>
              </div>
              <div className="button-row document-actions">
                <button
                  type="button"
                  className="button button-quiet"
                  disabled={busyId !== null}
                  aria-label={
                    document.status === "parsed"
                      ? `Extract facts again from ${document.display_name}`
                      : `Extract facts from ${document.display_name}`
                  }
                  onClick={() => onParse(document)}
                >
                  {busyId === document.id
                    ? "Working…"
                    : document.status === "parsed"
                      ? "Extract facts again"
                      : "Extract facts"}
                </button>
                <button
                  type="button"
                  className="button button-quiet"
                  disabled={busyId !== null}
                  aria-label={`Delete ${document.display_name}`}
                  onClick={() => onDelete(document)}
                >
                  Delete
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}

      <p className="fine-print">
        Deleting a document removes the stored file. Facts you confirmed are profile data and
        are kept; rejected and pending candidates from that document are no longer shown.
      </p>
    </div>
  );
}
