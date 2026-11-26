"use client";

// Plain anchors are deliberate in this client component: they keep the screen
// renderable in tests without the App Router.

import { useEffect, useRef, useState } from "react";

import {
  ApiError,
  approveCvAnalysis,
  createCvAnalysis,
  createCvOriginal,
  decideCvSuggestion,
  fetchShortlist,
  listCvVersions,
  listDocuments,
  previewCvAnalysis,
  type CvAnalysis,
  type CvDiffLine,
  type CvPreview,
  type CvSuggestion,
  type CvSuggestionStatus,
  type CvVersion,
  type DocumentRecord,
  type MatchTargetType,
  type ShortlistEntry,
} from "@/lib/api";
import { describeApiError } from "@/lib/errors";
import { formatDate } from "@/lib/format";

import { LoadingPanel, RetryPanel, SignedOutPanel } from "./account-panels";

type LoadState = "loading" | "ready" | "signed-out" | "failed";

const KIND_LABELS: Readonly<Record<CvSuggestion["kind"], string>> = {
  reorder_sections: "Reorder sections",
  reorder_bullets: "Reorder lines",
  surface_skill: "Add a confirmed skill",
  flag_vague: "Line with no result",
};

const STATUS_LABELS: Readonly<Record<CvSuggestionStatus, string>> = {
  pending: "Not decided",
  accepted: "Accepted",
  rejected: "Rejected",
  edited: "Edited",
};

function isSessionLost(failure: unknown): boolean {
  return failure instanceof ApiError && failure.status === 401;
}

function DiffView({ lines }: { readonly lines: readonly CvDiffLine[] }) {
  const changed = lines.filter((line) => line.op !== "same").length;
  if (changed === 0) {
    return <p className="hint">No change yet. Accept or edit a suggestion to see it here.</p>;
  }
  return (
    <ol className="cv-diff" aria-label="Changes against your original CV">
      {lines.map((line, index) => (
        <li key={`${index}-${line.op}`} className={`cv-diff-${line.op}`}>
          <span className="cv-diff-mark" aria-hidden="true">
            {line.op === "added" ? "+" : line.op === "removed" ? "−" : " "}
          </span>
          <span className="visually-hidden">
            {line.op === "added" ? "Added: " : line.op === "removed" ? "Removed: " : ""}
          </span>
          {line.line}
        </li>
      ))}
    </ol>
  );
}

interface SuggestionCardProps {
  readonly item: CvSuggestion;
  readonly busy: boolean;
  readonly onDecide: (item: CvSuggestion, status: CvSuggestionStatus, text?: string) => void;
}

function SuggestionCard({ item, busy, onDecide }: SuggestionCardProps) {
  const editable = item.kind === "surface_skill" || item.kind === "flag_vague";
  const [editing, setEditing] = useState(false);
  const [draft, setDraft] = useState(item.edited_text ?? item.after[0] ?? "");
  const decided = item.status !== "pending";
  const showsMove = item.kind === "reorder_sections" || item.kind === "reorder_bullets";

  return (
    <li className="card cv-suggestion">
      <div className="card-head">
        <h3>{KIND_LABELS[item.kind]}</h3>
        <span className={`badge ${decided ? "badge-available" : "badge-planned"}`}>
          {STATUS_LABELS[item.status]}
        </span>
      </div>
      <p>{item.reason}</p>
      {showsMove ? (
        <div className="cv-before-after">
          <div>
            <h4>Now</h4>
            <ol>
              {item.before.map((line, index) => (
                <li key={`b-${index}`}>{line}</li>
              ))}
            </ol>
          </div>
          <div>
            <h4>Suggested</h4>
            <ol>
              {item.after.map((line, index) => (
                <li key={`a-${index}`}>{line}</li>
              ))}
            </ol>
          </div>
        </div>
      ) : item.kind === "flag_vague" ? (
        <p className="research-meta">Your line: “{item.before[0]}”</p>
      ) : (
        <p className="research-meta">Would add: “{item.after[0]}”</p>
      )}
      {item.status === "edited" && item.edited_text ? (
        <p className="research-meta">Your wording: “{item.edited_text}”</p>
      ) : null}
      <details>
        <summary>Evidence ({item.evidence.length})</summary>
        <ul>
          {item.evidence.map((line) => (
            <li key={line}>{line}</li>
          ))}
        </ul>
      </details>
      {editing ? (
        <form
          className="field"
          onSubmit={(event) => {
            event.preventDefault();
            onDecide(item, "edited", draft);
            setEditing(false);
          }}
        >
          <label htmlFor={`cv-edit-${item.id}`}>In your own words</label>
          <textarea
            id={`cv-edit-${item.id}`}
            value={draft}
            maxLength={500}
            onChange={(event) => setDraft(event.target.value)}
          />
          <div className="button-row">
            <button type="submit" className="button" disabled={busy || !draft.trim()}>
              Save my wording
            </button>
            <button type="button" className="button button-quiet" onClick={() => setEditing(false)}>
              Cancel
            </button>
          </div>
        </form>
      ) : (
        <div className="button-row">
          {decided ? (
            <button
              type="button"
              className="button button-quiet"
              disabled={busy}
              aria-label={`Undo decision on ${KIND_LABELS[item.kind]} ${item.id}`}
              onClick={() => onDecide(item, "pending")}
            >
              Undo
            </button>
          ) : (
            <>
              <button
                type="button"
                className="button"
                disabled={busy}
                aria-label={`Accept ${KIND_LABELS[item.kind]} ${item.id}`}
                onClick={() => onDecide(item, "accepted")}
              >
                Accept
              </button>
              <button
                type="button"
                className="button button-quiet"
                disabled={busy}
                aria-label={`Reject ${KIND_LABELS[item.kind]} ${item.id}`}
                onClick={() => onDecide(item, "rejected")}
              >
                Reject
              </button>
              {editable ? (
                <button
                  type="button"
                  className="button button-quiet"
                  disabled={busy}
                  aria-label={`Edit ${KIND_LABELS[item.kind]} ${item.id}`}
                  onClick={() => setEditing(true)}
                >
                  Edit
                </button>
              ) : null}
            </>
          )}
        </div>
      )}
    </li>
  );
}

/**
 * Professor-specific CV review (ADR-024). Suggestions only reorder or reuse the
 * student's own content; nothing is applied until the student approves, and the
 * original CV is never changed.
 */
export function CvStudio() {
  const [state, setState] = useState<LoadState>("loading");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [cvDocuments, setCvDocuments] = useState<readonly DocumentRecord[]>([]);
  const [versions, setVersions] = useState<readonly CvVersion[]>([]);
  const [targets, setTargets] = useState<readonly ShortlistEntry[]>([]);
  const [analysis, setAnalysis] = useState<CvAnalysis | null>(null);
  const [preview, setPreview] = useState<CvPreview | null>(null);
  const [reloadToken, setReloadToken] = useState(0);
  const requestId = useRef(0);

  useEffect(() => {
    let cancelled = false;
    Promise.all([listDocuments(), listCvVersions(), fetchShortlist("shortlisted")])
      .then(([documentList, versionList, shortlist]) => {
        if (cancelled) {
          return;
        }
        setCvDocuments(documentList.documents.filter((record) => record.kind === "cv"));
        setVersions(versionList.versions);
        setTargets(shortlist.items.filter((entry) => entry.target_name !== null));
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

  const original = versions.find((version) => version.kind === "original") ?? null;
  const tailored = versions.filter((version) => version.kind === "tailored");

  function fail(failure: unknown): void {
    if (isSessionLost(failure)) {
      setState("signed-out");
      return;
    }
    setError(describeApiError(failure));
  }

  function prepareOriginal(document: DocumentRecord): void {
    setBusy(true);
    setError(null);
    createCvOriginal(document.id)
      .then((version) => setVersions((current) => [...current.filter((v) => v.id !== version.id), version]))
      .catch(fail)
      .finally(() => setBusy(false));
  }

  function openAnalysis(versionId: number, target: { type: MatchTargetType; id: number }, message?: string): void {
    const token = requestId.current + 1;
    requestId.current = token;
    setBusy(true);
    setError(null);
    setNotice(message ?? null);
    createCvAnalysis(versionId, target)
      .then((run) => Promise.all([run, previewCvAnalysis(run.id)]))
      .then(([run, nextPreview]) => {
        if (requestId.current !== token) {
          return;
        }
        setAnalysis(run);
        setPreview(nextPreview);
      })
      .catch(fail)
      .finally(() => {
        if (requestId.current === token) {
          setBusy(false);
        }
      });
  }

  /** Save one decision, patch that card in place, then re-read only the preview. */
  function decide(item: CvSuggestion, status: CvSuggestionStatus, text?: string): void {
    if (!analysis) {
      return;
    }
    const run = analysis;
    setBusy(true);
    setError(null);
    setNotice(null);
    decideCvSuggestion(item.id, status, text)
      .then((saved) => {
        setAnalysis({
          ...run,
          suggestions: run.suggestions.map((current) => (current.id === saved.id ? saved : current)),
        });
        return previewCvAnalysis(run.id);
      })
      .then(setPreview)
      .catch((failure: unknown) => {
        if (failure instanceof ApiError && failure.code === "cv_suggestion_missing") {
          // The run was replaced by a newer one (QA n3 on #46): reload it.
          openAnalysis(run.version_id, { type: run.target_type, id: run.target_id }, describeApiError(failure));
          return;
        }
        fail(failure);
      })
      .finally(() => setBusy(false));
  }

  function approve(): void {
    if (!analysis) {
      return;
    }
    setBusy(true);
    setError(null);
    approveCvAnalysis(analysis.id)
      .then((result) => {
        setVersions((current) => [...current.filter((v) => v.id !== result.version.id), result.version]);
        setNotice(`Approved a CV version for ${analysis.target_name}. Your original CV is unchanged.`);
      })
      .catch(fail)
      .finally(() => setBusy(false));
  }

  if (state === "loading") {
    return <LoadingPanel message="Loading your CV and shortlist…" />;
  }
  if (state === "signed-out") {
    return <SignedOutPanel intro="CV review uses your uploaded CV and profile, so it needs a signed-in account." />;
  }
  if (state === "failed") {
    return (
      <RetryPanel
        message={error ?? "Your CV could not be loaded."}
        onRetry={() => {
          setState("loading");
          setError(null);
          setReloadToken((token) => token + 1);
        }}
      />
    );
  }

  return (
    <div className="research-browser cv-studio">
      {error ? (
        <p className="alert" role="alert">
          {error}
        </p>
      ) : null}
      {notice ? (
        <p className="hint" role="status">
          {notice}
        </p>
      ) : null}

      <section aria-labelledby="cv-source-title">
        <h2 id="cv-source-title">1. Your CV</h2>
        {original ? (
          <p className="hint">
            Original prepared {formatDate(original.created_at)} with {original.sections.length} sections. It is
            never edited; approved versions are saved separately.
          </p>
        ) : cvDocuments.length === 0 ? (
          <p className="hint">
            No CV uploaded yet. <a href="/documents">Upload your CV</a> as a PDF, DOCX or TXT file first.
          </p>
        ) : (
          <ul className="research-list" role="list">
            {cvDocuments.map((document) => (
              <li key={document.id} className="card">
                <h3>{document.display_name}</h3>
                <button
                  type="button"
                  className="button"
                  disabled={busy}
                  onClick={() => prepareOriginal(document)}
                >
                  Use this CV
                </button>
              </li>
            ))}
          </ul>
        )}
      </section>

      {original ? (
        <section aria-labelledby="cv-target-title">
          <h2 id="cv-target-title">2. Tailor it for</h2>
          {targets.length === 0 ? (
            <p className="hint">
              Your shortlist is empty. <a href="/matches">Shortlist a professor or program</a> first.
            </p>
          ) : (
            <div className="button-row">
              {targets.map((entry) => (
                <button
                  key={entry.id}
                  type="button"
                  className="button button-quiet"
                  aria-pressed={analysis?.target_id === entry.target_id && analysis?.target_type === entry.target_type}
                  disabled={busy}
                  onClick={() =>
                    openAnalysis(original.id, { type: entry.target_type, id: entry.target_id })
                  }
                >
                  {entry.target_name}
                </button>
              ))}
            </div>
          )}
        </section>
      ) : null}

      {analysis ? (
        <section aria-labelledby="cv-suggestions-title">
          <h2 id="cv-suggestions-title">3. Suggestions for {analysis.target_name}</h2>
          {analysis.suggestions.length === 0 ? (
            <p className="hint">
              No suggestions: your CV already leads with the lines that match this research, and every
              matching confirmed skill is listed.
            </p>
          ) : (
            <ul className="research-list" role="list">
              {analysis.suggestions.map((item) => (
                <SuggestionCard key={item.id} item={item} busy={busy} onDecide={decide} />
              ))}
            </ul>
          )}
        </section>
      ) : null}

      {analysis && preview ? (
        <section aria-labelledby="cv-preview-title">
          <h2 id="cv-preview-title">4. Before and after</h2>
          <p className="counts">
            {preview.applied} {preview.applied === 1 ? "change" : "changes"} accepted · undecided suggestions are
            not applied
          </p>
          <DiffView lines={preview.diff} />
          <button type="button" className="button" disabled={busy} onClick={approve}>
            Approve this version for {analysis.target_name}
          </button>
        </section>
      ) : null}

      <section aria-labelledby="cv-versions-title">
        <h2 id="cv-versions-title">Approved versions</h2>
        {tailored.length === 0 ? (
          <p className="hint">None yet. A version is saved only when you approve it.</p>
        ) : (
          <ul className="research-list" role="list">
            {tailored.map((version) => (
              <li key={version.id} className="card">
                <h3>
                  Version {version.id} · {version.target_type === "faculty" ? "faculty" : "program"} {version.target_id}
                </h3>
                <p className="hint">Approved {formatDate(version.approved_at)}</p>
              </li>
            ))}
          </ul>
        )}
      </section>
    </div>
  );
}
