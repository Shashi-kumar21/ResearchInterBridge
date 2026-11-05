"use client";

// Plain anchors are deliberate in this client component: they keep the screen
// renderable in tests without the App Router.

import { useEffect, useState } from "react";

import {
  ApiError,
  applyFacts,
  listFacts,
  updateFact,
  type FactCandidate,
  type FactGroup,
} from "@/lib/api";
import { describeApiError } from "@/lib/errors";

import { LoadingPanel, RetryPanel, SignedOutPanel } from "./account-panels";

type LoadState = "loading" | "ready" | "signed-out" | "failed";

const FIELD_LABELS: Record<string, string> = {
  contact_email: "Contact email",
  phone: "Phone",
  gpa: "CGPA / GPA",
  gpa_scale: "Grading scale",
  expected_graduation_year: "Expected graduation year",
};

// Mirrors facts.APPLICABLE_FIELDS in the backend. The API remains the authority:
// this set only decides which hint is shown, never what is applied.
const APPLICABLE_FIELDS = new Set(["contact_email", "phone", "gpa"]);

function humaniseField(field: string): string {
  const known = FIELD_LABELS[field];
  if (known) {
    return known;
  }
  const words = field.replaceAll("_", " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

function isSessionLost(failure: unknown): boolean {
  return failure instanceof ApiError && failure.status === 401;
}

function confidenceText(confidence: number | null): string {
  if (confidence === null) {
    return "confidence not scored";
  }
  return `${Math.round(confidence * 100)}% confidence`;
}

/**
 * Name the file a candidate came from.
 *
 * `source` is an internal `document:3` id and is never shown. The API also sends
 * the display name; when it is absent the document has been deleted and the
 * fact is one the student had already confirmed.
 */
function sourceLabel(candidate: FactCandidate): string {
  return candidate.source_name
    ? `From ${candidate.source_name}`
    : "From a document you deleted";
}

interface PendingFactProps {
  readonly candidate: FactCandidate;
  readonly busy: boolean;
  readonly onConfirm: (factId: number, correctedValue: string) => void;
  readonly onReject: (factId: number) => void;
}

function PendingFact({ candidate, busy, onConfirm, onReject }: PendingFactProps) {
  const [correction, setCorrection] = useState("");
  const correctionId = `fact-${candidate.id}-correction`;
  return (
    <li>
      <div>
        <p className="fact-value">{candidate.value}</p>
        <p className="hint">
          {sourceLabel(candidate)} · {confidenceText(candidate.confidence)}
        </p>
        <div className="field fact-correction">
          {/* The visible label is the same on every row, so the input's
              accessible name names the extracted value too: a screen reader
              must be able to tell two "Correction" fields apart. */}
          <label htmlFor={correctionId}>Correction (optional)</label>
          <input
            id={correctionId}
            name={correctionId}
            value={correction}
            aria-label={`Correction for ${candidate.value} (optional)`}
            placeholder="Leave blank to keep the extracted value"
            onChange={(event) => setCorrection(event.target.value)}
          />
        </div>
      </div>
      <div className="button-row fact-actions">
        <button
          type="button"
          className="button"
          disabled={busy}
          aria-label={`Confirm ${candidate.value}`}
          onClick={() => onConfirm(candidate.id, correction)}
        >
          Confirm
        </button>
        <button
          type="button"
          className="button button-quiet"
          disabled={busy}
          aria-label={`Reject ${candidate.value}`}
          onClick={() => onReject(candidate.id)}
        >
          Reject
        </button>
      </div>
    </li>
  );
}

interface ConfirmedFactProps {
  readonly candidate: FactCandidate;
  readonly busy: boolean;
  readonly onCorrect: (factId: number, correctedValue: string) => void;
}

/**
 * A confirmed fact, with a correction path.
 *
 * The server refuses to reject a confirmed fact, so without this the only way to
 * fix a wrong confirmed value would be a competing candidate, which does not
 * exist when one document proposed it. A correction re-confirms the same row
 * with a new value and is applied to the profile on the next apply.
 */
function ConfirmedFact({ candidate, busy, onCorrect }: ConfirmedFactProps) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(candidate.value);
  const inputId = `fact-${candidate.id}-edit`;

  function save(): void {
    const next = value.trim();
    if (next === "") {
      return;
    }
    setEditing(false);
    if (next === candidate.value) {
      // Nothing to send: re-confirming the same value would look like a change.
      return;
    }
    onCorrect(candidate.id, next);
  }

  return (
    <li>
      <div>
        <p className="fact-value">{candidate.value}</p>
        <p className="hint">
          {sourceLabel(candidate)} · {confidenceText(candidate.confidence)}
        </p>
        {editing ? (
          <div className="field fact-correction">
            <label htmlFor={inputId}>Corrected value</label>
            <input
              id={inputId}
              name={inputId}
              value={value}
              aria-label={`Corrected value for ${candidate.value}`}
              onChange={(event) => setValue(event.target.value)}
            />
          </div>
        ) : null}
      </div>
      <div className="button-row fact-actions">
        {editing ? (
          <>
            <button
              type="button"
              className="button"
              disabled={busy || value.trim() === ""}
              aria-label={`Save correction to ${candidate.value}`}
              onClick={save}
            >
              Save correction
            </button>
            <button
              type="button"
              className="button button-quiet"
              disabled={busy}
              aria-label={`Cancel correction to ${candidate.value}`}
              onClick={() => {
                setValue(candidate.value);
                setEditing(false);
              }}
            >
              Cancel
            </button>
          </>
        ) : (
          <>
            <span className="granted">Confirmed</span>
            <button
              type="button"
              className="button button-quiet"
              disabled={busy}
              aria-label={`Correct ${candidate.value}`}
              onClick={() => setEditing(true)}
            >
              Correct
            </button>
          </>
        )}
      </div>
    </li>
  );
}

/**
 * Fact review.
 *
 * Extraction proposes; the student decides. Confirming makes one candidate the
 * only confirmed truth for its field (the server rejects the others), and only
 * confirmed facts can be copied into the profile or used in a draft. The page
 * refreshes the whole list after one decision because one confirmation can
 * change several rows.
 */
export function FactReview() {
  const [state, setState] = useState<LoadState>("loading");
  const [groups, setGroups] = useState<readonly FactGroup[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<number | null>(null);
  const [applying, setApplying] = useState(false);
  const [overwrite, setOverwrite] = useState(false);
  const [reloadToken, setReloadToken] = useState(0);

  useEffect(() => {
    let cancelled = false;
    listFacts()
      .then((list) => {
        if (cancelled) {
          return;
        }
        setGroups(list.groups);
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
    setGroups([]);
    setState("loading");
    setReloadToken((token) => token + 1);
  }

  async function refreshFacts(): Promise<void> {
    try {
      const list = await listFacts();
      setGroups(list.groups);
    } catch (failure) {
      if (isSessionLost(failure)) {
        setState("signed-out");
        return;
      }
      setError(describeApiError(failure));
    }
  }

  async function onConfirm(factId: number, correctedValue: string): Promise<void> {
    setBusyId(factId);
    setError(null);
    setNotice(null);
    try {
      const result = await updateFact(factId, {
        status: "confirmed",
        correctedValue,
      });
      await refreshFacts();
      setNotice(
        correctedValue.trim()
          ? result.already_set
            ? "Fact corrected. The new value replaced the one you had confirmed."
            : "Fact confirmed with your correction. Competing values are hidden."
          : result.already_set
            ? "That fact was already confirmed."
            : "Fact confirmed. It may now appear in a draft.",
      );
    } catch (failure) {
      if (isSessionLost(failure)) {
        setState("signed-out");
      } else {
        setError(describeApiError(failure));
      }
    } finally {
      setBusyId(null);
    }
  }

  async function onReject(factId: number): Promise<void> {
    setBusyId(factId);
    setError(null);
    setNotice(null);
    try {
      const result = await updateFact(factId, { status: "rejected" });
      await refreshFacts();
      setNotice(
        result.already_set
          ? "That fact was already rejected."
          : "Fact rejected. It will not be used.",
      );
    } catch (failure) {
      if (isSessionLost(failure)) {
        setState("signed-out");
      } else {
        setError(describeApiError(failure));
      }
    } finally {
      setBusyId(null);
    }
  }

  async function onApply(): Promise<void> {
    setApplying(true);
    setError(null);
    setNotice(null);
    try {
      const result = await applyFacts({ overwrite });
      setNotice(
        result.applied.length === 0
          ? "Nothing was applied: no applicable fact is confirmed, or those profile fields are already filled in. Tick overwrite to replace typed values."
          : `Applied to your profile: ${result.applied.map(humaniseField).join(", ")}. Check the profile for the new values.`,
      );
    } catch (failure) {
      if (isSessionLost(failure)) {
        setState("signed-out");
      } else {
        setError(describeApiError(failure));
      }
    } finally {
      setApplying(false);
    }
  }

  if (state === "loading") {
    return <LoadingPanel message="Loading extracted facts…" />;
  }

  if (state === "signed-out") {
    return <SignedOutPanel intro="Extracted facts belong to a signed-in account." />;
  }

  if (state === "failed") {
    return <RetryPanel message={error ?? "Your facts could not be loaded."} onRetry={retry} />;
  }

  const confirmedApplicable = groups.some(
    (group) => group.confirmed.length > 0 && APPLICABLE_FIELDS.has(group.field),
  );

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

      {groups.length === 0 ? (
        <p className="hint">
          No candidate facts yet. Upload a CV or transcript, then choose “Extract facts” on the
          documents page.
        </p>
      ) : (
        <>
          <p className="hint">
            A confirmed fact is the only truth for its field: confirming one candidate rejects
            competing values. Only confirmed contact email, phone and GPA can be copied to the
            profile automatically; a graduation year needs a full date.
          </p>
          {groups.map((group) => (
            <section
              className="fact-group"
              key={group.field}
              aria-labelledby={`fact-field-${group.field}`}
            >
              <div className="section-head">
                <h2 id={`fact-field-${group.field}`}>{humaniseField(group.field)}</h2>
                {group.has_conflict ? (
                  <span className="badge badge-in-progress">Conflict</span>
                ) : null}
              </div>
              {group.has_conflict ? (
                <p className="alert" role="status">
                  Two documents propose different values. Confirm the one that is true.
                </p>
              ) : null}

              {group.confirmed.length > 0 ? (
                <div className="fact-confirmed">
                  <h3>Confirmed</h3>
                  <ul className="status-list" role="list">
                    {group.confirmed.map((candidate) => (
                      <ConfirmedFact
                        key={`${candidate.id}:${candidate.value}`}
                        candidate={candidate}
                        busy={busyId !== null}
                        onCorrect={onConfirm}
                      />
                    ))}
                  </ul>
                  <p className="hint">
                    A confirmed fact cannot be rejected, but it can be corrected: “Correct” saves a
                    new value in place. A document you delete keeps the facts you confirmed.
                  </p>
                </div>
              ) : null}

              {group.pending.length > 0 ? (
                <div className="fact-pending">
                  <h3>Waiting for your decision</h3>
                  <ul className="status-list" role="list">
                    {group.pending.map((candidate) => (
                      <PendingFact
                        key={candidate.id}
                        candidate={candidate}
                        busy={busyId !== null}
                        onConfirm={onConfirm}
                        onReject={onReject}
                      />
                    ))}
                  </ul>
                </div>
              ) : null}

              {!APPLICABLE_FIELDS.has(group.field) && group.confirmed.length > 0 ? (
                <p className="hint">
                  This field is kept for review but is not copied to the profile automatically.
                </p>
              ) : null}
            </section>
          ))}
        </>
      )}

      <div className="bookkeeping">
        <h2>Use confirmed facts</h2>
        <p className="hint">
          {confirmedApplicable
            ? "Blank profile fields are filled in. Values you typed yourself are kept unless you tick overwrite."
            : "Confirm a contact email, phone or GPA first. Applying does not add a profile that has not been saved."}
        </p>
        <div className="check">
          <input
            id="fact-overwrite"
            name="fact-overwrite"
            type="checkbox"
            checked={overwrite}
            onChange={(event) => setOverwrite(event.target.checked)}
          />
          <label htmlFor="fact-overwrite">
            Overwrite profile values I typed myself
          </label>
        </div>
        <div className="button-row">
          <button
            type="button"
            className="button"
            disabled={applying || !confirmedApplicable}
            onClick={onApply}
          >
            {applying ? "Applying…" : "Apply confirmed facts to profile"}
          </button>
          <a href="/onboarding">Check the profile</a>
          <a href="/documents">Back to documents</a>
        </div>
      </div>
    </div>
  );
}
