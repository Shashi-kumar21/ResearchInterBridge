"use client";

// Plain anchors are deliberate in this client component: they keep the screen
// renderable in tests without the App Router. Every draft field is rendered as
// React text (escaped); nothing uses dangerouslySetInnerHTML.

import { useEffect, useRef, useState } from "react";

import {
  ApiError,
  approveDraft,
  createDraft,
  fetchShortlist,
  getDraft,
  listCvVersions,
  listDrafts,
  saveDraft,
  withdrawDraftApproval,
  type DraftSummary,
  type EmailDraft,
  type LintFinding,
  type ShortlistEntry,
} from "@/lib/api";
import { describeApiError } from "@/lib/errors";
import { formatDate } from "@/lib/format";

import { LoadingPanel, RetryPanel, SignedOutPanel } from "./account-panels";

type LoadState = "loading" | "ready" | "signed-out" | "failed";

function isSessionLost(failure: unknown): boolean {
  return failure instanceof ApiError && failure.status === 401;
}

/** Only http(s) sources become links; anything else is shown as text. */
function SourceLink({ source }: { readonly source: string }) {
  if (/^https?:\/\//i.test(source)) {
    return (
      <a href={source} rel="noreferrer noopener" target="_blank">
        source
      </a>
    );
  }
  return <span>{source}</span>;
}

function LintList({ findings }: { readonly findings: readonly LintFinding[] }) {
  if (findings.length === 0) {
    return <p className="hint">No issues found. This draft passes every deliverability check.</p>;
  }
  return (
    <ul className="draft-lint" aria-label="Deliverability checks">
      {findings.map((finding) => (
        <li key={`${finding.code}-${finding.message}`} className={`draft-lint-${finding.level}`}>
          <strong>{finding.level === "block" ? "Must fix: " : "Suggestion: "}</strong>
          {finding.message}
        </li>
      ))}
    </ul>
  );
}

/**
 * The email studio (ADR-026): one draft per shortlisted professor, built from
 * verified facts. The student edits plain text, sees the §14.2 checks next to
 * it, and approves the exact version; any change clears the approval. Nothing
 * is sent from this screen.
 */
export function DraftStudio() {
  const [state, setState] = useState<LoadState>("loading");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [summaries, setSummaries] = useState<readonly DraftSummary[]>([]);
  const [targets, setTargets] = useState<readonly ShortlistEntry[]>([]);
  const [hasCv, setHasCv] = useState(false);
  const [draft, setDraft] = useState<EmailDraft | null>(null);
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [viewing, setViewing] = useState<number | null>(null);
  const [reloadToken, setReloadToken] = useState(0);
  const requestId = useRef(0);

  useEffect(() => {
    let cancelled = false;
    Promise.all([listDrafts(), fetchShortlist("shortlisted"), listCvVersions()])
      .then(([draftList, shortlist, cvList]) => {
        if (cancelled) {
          return;
        }
        setSummaries(draftList.drafts);
        setTargets(shortlist.items.filter((entry) => entry.target_type === "faculty" && entry.target_name));
        setHasCv(cvList.versions.length > 0);
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

  const dirty = draft !== null && (subject !== draft.current.subject || body !== draft.current.body);
  const blocking = draft ? draft.lint.filter((finding) => finding.level === "block") : [];

  function fail(failure: unknown): void {
    if (isSessionLost(failure)) {
      setState("signed-out");
      return;
    }
    setError(describeApiError(failure));
  }

  /** Show a draft returned by the API and refresh its row in the list. */
  function show(next: EmailDraft, message: string | null = null): void {
    setDraft(next);
    setSubject(next.current.subject);
    setBody(next.current.body);
    setViewing(null);
    setNotice(message);
    setSummaries((current) => {
      const row: DraftSummary = {
        id: next.id,
        target_id: next.target_id,
        target_name: next.target_name,
        subject: next.current.subject,
        version_no: next.current.version_no,
        recipient: next.current.recipient,
        flag_count: next.current.flags.length,
        approved: next.approval !== null,
        updated_at: next.updated_at,
      };
      return [row, ...current.filter((item) => item.id !== next.id)];
    });
  }

  function run(action: () => Promise<EmailDraft>, message: (next: EmailDraft) => string | null): void {
    const token = requestId.current + 1;
    requestId.current = token;
    setBusy(true);
    setError(null);
    action()
      .then((next) => {
        if (requestId.current === token) {
          show(next, message(next));
        }
      })
      .catch((failure: unknown) => {
        if (requestId.current !== token) {
          return;
        }
        // The draft changed or the lint blocked: reload it so the student sees
        // the current text and the exact checks to fix (QA n3 on #53).
        if (
          draft &&
          failure instanceof ApiError &&
          (failure.code === "draft_changed" || failure.code === "draft_lint_blocked")
        ) {
          getDraft(draft.id)
            .then((fresh) => show(fresh, describeApiError(failure)))
            .catch(fail);
          return;
        }
        fail(failure);
      })
      .finally(() => {
        if (requestId.current === token) {
          setBusy(false);
        }
      });
  }

  function open(targetId: number): void {
    const existing = summaries.find((row) => row.target_id === targetId);
    if (existing) {
      run(() => getDraft(existing.id), () => null);
    } else {
      run(() => createDraft(targetId), () => "Draft created from verified facts. Review and edit it.");
    }
  }

  function save(): void {
    if (!draft) {
      return;
    }
    const wasApproved = draft.approval !== null;
    run(
      () => saveDraft(draft.id, subject, body),
      () => (wasApproved ? "Saved. Your edit cleared the approval; review and approve again." : "Saved as a new version."),
    );
  }

  if (state === "loading") {
    return <LoadingPanel message="Loading your drafts and shortlist…" />;
  }
  if (state === "signed-out") {
    return <SignedOutPanel intro="Drafts use your profile and CV, so they need a signed-in account." />;
  }
  if (state === "failed") {
    return (
      <RetryPanel
        message={error ?? "Your drafts could not be loaded."}
        onRetry={() => {
          setState("loading");
          setError(null);
          setReloadToken((token) => token + 1);
        }}
      />
    );
  }

  return (
    <div className="research-browser draft-studio">
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

      <section aria-labelledby="draft-targets-title">
        <h2 id="draft-targets-title">Professors</h2>
        {!hasCv ? (
          <p className="hint">
            No CV prepared yet, so drafts will have nothing attached. <a href="/cv">Prepare your CV</a>.
          </p>
        ) : null}
        {targets.length === 0 ? (
          <p className="hint">
            Your shortlist has no professors yet. <a href="/matches">Shortlist a professor</a> first.
          </p>
        ) : (
          <ul className="research-list" role="list">
            {targets.map((entry) => {
              const row = summaries.find((item) => item.target_id === entry.target_id);
              return (
                <li key={entry.id} className="card">
                  <div className="card-head">
                    <h3>{entry.target_name}</h3>
                    {row ? (
                      <span className={`badge ${row.approved ? "badge-available" : "badge-planned"}`}>
                        {row.approved ? "Approved" : `Draft v${row.version_no}`}
                      </span>
                    ) : null}
                  </div>
                  {row && row.recipient === null ? (
                    <p className="hint">No verified email yet; this draft cannot be approved.</p>
                  ) : null}
                  <button
                    type="button"
                    className="button button-quiet"
                    disabled={busy || dirty}
                    aria-label={`${row ? "Open draft for" : "Create draft for"} ${entry.target_name}`}
                    onClick={() => open(entry.target_id)}
                  >
                    {row ? "Open draft" : "Create draft"}
                  </button>
                </li>
              );
            })}
          </ul>
        )}
        {dirty ? <p className="hint">Save or discard your changes before opening another draft.</p> : null}
      </section>

      {draft ? (
        <section aria-labelledby="draft-editor-title">
          <h2 id="draft-editor-title">Email to {draft.target_name}</h2>
          <p className="hint">
            To: {draft.current.recipient ?? "no verified email yet"} · version {draft.current.version_no} ·{" "}
            {draft.current.cv_version_id ? "CV attached" : "no CV attached"}
          </p>
          {draft.approval ? (
            <p className="draft-approved" role="status">
              Approved version {draft.approval.version_no} on {formatDate(draft.approval.approved_at)}.
              Nothing has been sent; sending comes later.
            </p>
          ) : (
            <p className="hint">Not approved. Approval applies to this exact text, recipient and attachment.</p>
          )}
          <div className="field">
            <label htmlFor="draft-subject">Subject</label>
            <input
              id="draft-subject"
              value={subject}
              maxLength={200}
              onChange={(event) => setSubject(event.target.value)}
            />
          </div>
          <div className="field">
            <label htmlFor="draft-body">Message</label>
            <textarea
              id="draft-body"
              value={body}
              maxLength={10000}
              rows={14}
              onChange={(event) => setBody(event.target.value)}
            />
          </div>
          <div className="button-row">
            <button type="button" className="button" disabled={busy || !dirty} onClick={save}>
              Save changes
            </button>
            <button
              type="button"
              className="button button-quiet"
              disabled={busy || !dirty}
              onClick={() => {
                setSubject(draft.current.subject);
                setBody(draft.current.body);
              }}
            >
              Discard changes
            </button>
            <button
              type="button"
              className="button button-quiet"
              disabled={busy || dirty}
              onClick={() =>
                run(() => createDraft(draft.target_id), () => "Regenerated from verified facts as a new version.")
              }
            >
              Regenerate
            </button>
          </div>

          <h3>Deliverability checks</h3>
          {dirty ? <p className="hint">Save to re-check your changes.</p> : null}
          <LintList findings={draft.lint} />

          {draft.current.flags.length > 0 ? (
            <>
              <h3>Missing or unverified details</h3>
              <ul>
                {draft.current.flags.map((flag) => (
                  <li key={flag}>{flag}</li>
                ))}
              </ul>
            </>
          ) : null}

          <details>
            <summary>Evidence ({draft.current.evidence.length})</summary>
            <ul>
              {draft.current.evidence.map((item) => (
                <li key={item.claim}>
                  {item.claim} · <SourceLink source={item.source} />
                </li>
              ))}
            </ul>
          </details>

          <div className="button-row">
            {draft.approval ? (
              <button
                type="button"
                className="button button-quiet"
                disabled={busy}
                onClick={() => run(() => withdrawDraftApproval(draft.id), () => "Approval withdrawn.")}
              >
                Withdraw approval
              </button>
            ) : (
              <button
                type="button"
                className="button"
                disabled={busy || dirty || blocking.length > 0 || draft.current.recipient === null}
                onClick={() =>
                  run(
                    () => approveDraft(draft.id, draft.current.version_no),
                    (next) => `Approved version ${next.current.version_no}.`,
                  )
                }
              >
                Approve this exact version
              </button>
            )}
          </div>
          {!draft.approval && blocking.length > 0 ? (
            <p className="hint">Fix the “Must fix” items above to enable approval.</p>
          ) : null}

          <details>
            <summary>Version history ({draft.history.length})</summary>
            <ul className="research-list" role="list">
              {draft.history.map((version) => (
                <li key={version.version_no} className="card">
                  <p className="hint">
                    Version {version.version_no} · {version.source === "generated" ? "generated" : "your edit"} ·{" "}
                    {formatDate(version.created_at)}
                  </p>
                  <button
                    type="button"
                    className="button button-quiet"
                    aria-expanded={viewing === version.version_no}
                    onClick={() => setViewing(viewing === version.version_no ? null : version.version_no)}
                  >
                    {viewing === version.version_no ? "Hide" : "View"} version {version.version_no}
                  </button>
                  {viewing === version.version_no ? (
                    <div className="draft-version">
                      <p>
                        <strong>Subject:</strong> {version.subject}
                      </p>
                      <pre className="draft-body">{version.body}</pre>
                    </div>
                  ) : null}
                </li>
              ))}
            </ul>
          </details>
        </section>
      ) : null}
    </div>
  );
}
