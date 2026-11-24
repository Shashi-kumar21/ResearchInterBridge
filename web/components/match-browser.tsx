"use client";

// Plain anchors are deliberate in this client component: they keep the screen
// renderable in tests without the App Router.

import { useEffect, useRef, useState, type FormEvent } from "react";

import {
  ApiError,
  addToShortlist,
  excludeFromMatches,
  fetchShortlist,
  listFacultyMatches,
  listProgramMatches,
  refreshShortlist,
  removeFromShortlist,
  type FacultyMatch,
  type MatchExplanation,
  type MatchFactor,
  type MatchPage,
  type MatchTargetType,
  type ProgramMatch,
  type ShortlistEntry,
  type ShortlistTarget,
} from "@/lib/api";
import { describeApiError } from "@/lib/errors";
import { formatDate } from "@/lib/format";

import { LoadingPanel, RetryPanel, SignedOutPanel } from "./account-panels";

type LoadState = "loading" | "ready" | "signed-out" | "no-profile" | "failed";
type Tab = "faculty" | "programs";

/** Page size for the ranked list. The API caps a page at 100. */
const PAGE_SIZE = 10;
/** Largest page the API serves; used to re-read every loaded row at once. */
const MAX_PAGE = 100;
/** The API accepts at most 50 targets per shortlist request. */
const MAX_SELECTION = 50;

/** One row of either list, reduced to what the card needs. */
interface MatchRow {
  readonly target: ShortlistTarget;
  readonly name: string;
  readonly context: string;
  readonly link: string;
  readonly linkLabel: string;
  readonly stale: boolean;
  readonly match: MatchExplanation;
  readonly shortlistStatus: "shortlisted" | "excluded" | null;
}

interface Filters {
  readonly area: string;
  readonly country: string;
}

const NO_FILTERS: Filters = { area: "", country: "" };

function stated(value: string | null | undefined): string {
  return value && value.trim() ? value : "Not stated";
}

function facultyRow(item: FacultyMatch): MatchRow {
  const person = item.faculty;
  return {
    target: { type: "faculty", id: person.id },
    name: person.full_name,
    context: `${person.institution} · ${stated(person.department)} · ${stated(person.country)}`,
    link: person.profile_url,
    linkLabel: "Official profile",
    stale: person.stale,
    match: item.match,
    shortlistStatus: item.shortlist_status,
  };
}

function programRow(item: ProgramMatch): MatchRow {
  const program = item.program;
  return {
    target: { type: "program", id: program.id },
    name: program.name,
    context: `${stated(program.institution)} · ${stated(program.department)} · ${stated(program.country)}`,
    link: program.official_url,
    linkLabel: "Official page",
    stale: program.stale,
    match: item.match,
    shortlistStatus: item.shortlist_status,
  };
}

function keyOf(target: ShortlistTarget): string {
  return `${target.type}:${target.id}`;
}

function eligibilityLabel(match: MatchExplanation): string {
  switch (match.eligibility) {
    case "eligible":
      return "Eligible";
    case "ineligible":
      return "Not eligible";
    case "unknown":
      return "Eligibility unknown";
    default:
      return "No eligibility conditions";
  }
}

function factorStatusLabel(factor: MatchFactor): string {
  switch (factor.status) {
    case "met":
      return "Met";
    case "partial":
      return "Partly met";
    case "unmet":
      return "Not met";
    case "unknown":
      return "Unknown";
    default:
      return "Does not apply";
  }
}

function Explanation({ match }: { readonly match: MatchExplanation }) {
  const failed = match.gates.filter((gate) => gate.status === "fail");
  const factors = match.factors.filter((factor) => factor.status !== "not_applicable");
  return (
    <div className="research-detail">
      {failed.length > 0 ? (
        <ul className="match-gates" aria-label="Failed eligibility conditions">
          {failed.map((gate) => (
            <li key={gate.key} className="alert">
              {gate.label}: {gate.reason}
            </li>
          ))}
        </ul>
      ) : null}
      <details>
        <summary>Why this score</summary>
        <table className="match-factors">
          <thead>
            <tr>
              <th scope="col">Factor</th>
              <th scope="col">Result</th>
              <th scope="col">Points</th>
              <th scope="col">Reason</th>
            </tr>
          </thead>
          <tbody>
            {factors.map((factor) => (
              <tr key={factor.key}>
                <th scope="row">{factor.label}</th>
                <td>{factorStatusLabel(factor)}</td>
                <td>
                  {Math.round(factor.points * 10) / 10} / {factor.weight}
                </td>
                <td>{factor.reason}</td>
              </tr>
            ))}
          </tbody>
        </table>
        {match.gates.length > 0 ? (
          <ul aria-label="Eligibility conditions">
            {match.gates.map((gate) => (
              <li key={gate.key}>
                {gate.label}: {gate.status === "pass" ? "met" : gate.status === "fail" ? "not met" : "unknown"} —{" "}
                {gate.reason}
              </li>
            ))}
          </ul>
        ) : null}
      </details>
      {match.gaps.length > 0 ? (
        <details>
          <summary>Unknowns ({match.gaps.length})</summary>
          <ul>
            {match.gaps.map((gap) => (
              <li key={gap}>{gap}</li>
            ))}
          </ul>
        </details>
      ) : null}
    </div>
  );
}

interface MatchCardProps {
  readonly row: MatchRow;
  readonly selected: boolean;
  /** True when the selection is full and this row is not part of it. */
  readonly selectionFull: boolean;
  readonly busy: boolean;
  readonly onSelect: (row: MatchRow, selected: boolean) => void;
  readonly onShortlist: (row: MatchRow) => void;
  readonly onExclude: (row: MatchRow) => void;
}

function MatchCard({
  row,
  selected,
  selectionFull,
  busy,
  onSelect,
  onShortlist,
  onExclude,
}: MatchCardProps) {
  const ineligible = row.match.eligibility === "ineligible";
  return (
    <li className="card research-match">
      <div className="card-head">
        <label className="match-select">
          <input
            type="checkbox"
            checked={selected}
            disabled={busy || row.shortlistStatus === "shortlisted" || selectionFull}
            aria-label={`Select ${row.name}`}
            onChange={(event) => onSelect(row, event.target.checked)}
          />
          <h3>{row.name}</h3>
        </label>
        <span
          className={`badge ${ineligible ? "badge-planned" : "badge-available"}`}
          aria-label={`Score ${row.match.score} of 100`}
        >
          {row.match.score}/100
        </span>
      </div>
      <p className="hint">{row.context}</p>
      <p className="research-meta">
        {eligibilityLabel(row.match)}
        {row.shortlistStatus === "shortlisted" ? " · On your shortlist" : ""}
        {row.shortlistStatus === "excluded" ? " · Excluded" : ""}
        {row.stale ? " · Stale record" : ""}
      </p>
      <Explanation match={row.match} />
      <p className="hint">
        Scored {formatDate(row.match.evaluated_on)} ·{" "}
        <a href={row.link} rel="noreferrer noopener" target="_blank">
          {row.linkLabel}
        </a>
      </p>
      <div className="button-row">
        <button
          type="button"
          className="button"
          disabled={busy || row.shortlistStatus === "shortlisted"}
          aria-label={`Shortlist ${row.name}`}
          onClick={() => onShortlist(row)}
        >
          {row.shortlistStatus === "shortlisted" ? "Shortlisted" : "Shortlist"}
        </button>
        <button
          type="button"
          className="button button-quiet"
          disabled={busy || row.shortlistStatus === "excluded"}
          aria-label={`Exclude ${row.name}`}
          onClick={() => onExclude(row)}
        >
          {row.shortlistStatus === "excluded" ? "Excluded" : "Exclude"}
        </button>
      </div>
    </li>
  );
}

function ShortlistRow({
  entry,
  busy,
  onRemove,
}: {
  readonly entry: ShortlistEntry;
  readonly busy: boolean;
  readonly onRemove: (entry: ShortlistEntry) => void;
}) {
  const name = entry.target_name ?? "No longer in the research cache";
  const snapshot = entry.snapshot;
  return (
    <li className="card research-shortlist">
      <div className="card-head">
        <h3>{name}</h3>
        {snapshot ? (
          <span className={`badge ${snapshot.eligibility === "ineligible" ? "badge-planned" : "badge-available"}`}>
            {snapshot.score}/100
          </span>
        ) : null}
      </div>
      <p className="hint">
        {entry.target_type === "faculty" ? "Faculty" : "Program"}
        {snapshot ? ` · saved score from ${formatDate(snapshot.evaluated_on)}` : ""}
        {entry.current === false ? " · Out of date: your profile or the record changed" : ""}
      </p>
      {snapshot && snapshot.eligibility === "ineligible" ? (
        <p className="alert">Not eligible when saved: {snapshot.gates.find((g) => g.status === "fail")?.reason}</p>
      ) : null}
      <button
        type="button"
        className="button button-quiet"
        disabled={busy}
        aria-label={`Remove ${name} from your shortlist`}
        onClick={() => onRemove(entry)}
      >
        Remove
      </button>
    </li>
  );
}

/**
 * Ranked, explained matches over the shared cache, plus the student's own
 * shortlist (ADR-022). Scores are computed by the API from the saved profile;
 * a snapshot is stored only when the student shortlists.
 */
export function MatchBrowser() {
  const [state, setState] = useState<LoadState>("loading");
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [tab, setTab] = useState<Tab>("faculty");
  const [draft, setDraft] = useState<Filters>(NO_FILTERS);
  const [filters, setFilters] = useState<Filters>(NO_FILTERS);
  const [rows, setRows] = useState<readonly MatchRow[]>([]);
  const [total, setTotal] = useState(0);
  const [truncated, setTruncated] = useState(false);
  const [selected, setSelected] = useState<ReadonlySet<string>>(new Set());
  const [shortlist, setShortlist] = useState<readonly ShortlistEntry[]>([]);
  const [excluded, setExcluded] = useState<readonly ShortlistEntry[]>([]);
  /** The target the last notice can undo (an exclusion), if any. */
  const [undoTarget, setUndoTarget] = useState<ShortlistTarget | null>(null);
  const [reloadToken, setReloadToken] = useState(0);
  const requestId = useRef(0);

  function loadPage(
    nextTab: Tab,
    nextFilters: Filters,
    offset: number,
    limit: number = PAGE_SIZE,
  ): Promise<MatchPage<MatchRow>> {
    const query = {
      area: nextFilters.area || undefined,
      country: nextFilters.country || undefined,
      limit,
      offset,
    };
    return nextTab === "faculty"
      ? listFacultyMatches(query).then((page) => ({ ...page, items: page.items.map(facultyRow) }))
      : listProgramMatches(query).then((page) => ({ ...page, items: page.items.map(programRow) }));
  }

  /** Re-read as many ranked rows as are on screen, in API-sized pages (QA n2 on #39). */
  async function reloadLoaded(count: number): Promise<MatchPage<MatchRow>> {
    const wanted = Math.max(PAGE_SIZE, count);
    let first = await loadPage(tab, filters, 0, Math.min(MAX_PAGE, wanted));
    let items = [...first.items];
    while (items.length < wanted && items.length < first.total) {
      const next = await loadPage(tab, filters, items.length, Math.min(MAX_PAGE, wanted - items.length));
      if (next.items.length === 0) {
        break;
      }
      items = [...items, ...next.items];
      first = { ...first, total: next.total, truncated: next.truncated };
    }
    return { ...first, items };
  }

  function applyShortlist(entries: readonly ShortlistEntry[]): void {
    setShortlist(entries.filter((entry) => entry.status === "shortlisted"));
    setExcluded(entries.filter((entry) => entry.status === "excluded"));
  }

  useEffect(() => {
    let cancelled = false;
    Promise.all([loadPage("faculty", NO_FILTERS, 0), fetchShortlist()])
      .then(([page, list]) => {
        if (cancelled) {
          return;
        }
        setRows(page.items);
        setTotal(page.total);
        setTruncated(page.truncated);
        applyShortlist(list.items);
        setState("ready");
      })
      .catch((failure: unknown) => {
        if (cancelled) {
          return;
        }
        if (failure instanceof ApiError && failure.status === 401) {
          setState("signed-out");
          return;
        }
        if (failure instanceof ApiError && failure.code === "profile_missing") {
          setState("no-profile");
          return;
        }
        setError(describeApiError(failure));
        setState("failed");
      });
    return () => {
      cancelled = true;
    };
  }, [reloadToken]);

  function fail(failure: unknown): void {
    if (failure instanceof ApiError && failure.status === 401) {
      setState("signed-out");
      return;
    }
    setError(describeApiError(failure));
  }

  function show(nextTab: Tab, nextFilters: Filters): void {
    const token = requestId.current + 1;
    requestId.current = token;
    setTab(nextTab);
    setFilters(nextFilters);
    setSelected(new Set());
    setBusy(true);
    setError(null);
    loadPage(nextTab, nextFilters, 0)
      .then((page) => {
        if (requestId.current !== token) {
          return;
        }
        setRows(page.items);
        setTotal(page.total);
        setTruncated(page.truncated);
      })
      .catch(fail)
      .finally(() => {
        if (requestId.current === token) {
          setBusy(false);
        }
      });
  }

  function loadMore(): void {
    const token = requestId.current + 1;
    requestId.current = token;
    setBusy(true);
    setError(null);
    loadPage(tab, filters, rows.length)
      .then((page) => {
        if (requestId.current !== token) {
          return;
        }
        setRows((current) => [...current, ...page.items]);
        setTotal(page.total);
        setTruncated(page.truncated);
      })
      .catch(fail)
      .finally(() => {
        if (requestId.current === token) {
          setBusy(false);
        }
      });
  }

  /**
   * Run a shortlist change. The changed cards are patched in place and only the
   * shortlist is re-read, so loaded pages stay and nothing is re-scored (QA n2
   * on #39; Owner priority: low latency, low cost). Only a restore re-reads the
   * loaded rows, because the restored entry must reappear at its rank. A
   * request token drops a stale result after a tab or filter change (QA n3).
   */
  function change(
    action: () => Promise<unknown>,
    message: string,
    options: {
      readonly patch?: (current: readonly MatchRow[]) => readonly MatchRow[];
      readonly rerank?: boolean;
      readonly undo?: ShortlistTarget;
    } = {},
  ): void {
    const token = requestId.current + 1;
    requestId.current = token;
    const before = rows;
    const loaded = rows.length;
    setBusy(true);
    setError(null);
    setNotice(null);
    setUndoTarget(null);
    action()
      .then(() =>
        Promise.all([options.rerank ? reloadLoaded(loaded) : Promise.resolve(null), fetchShortlist()]),
      )
      .then(([page, list]) => {
        if (requestId.current !== token) {
          return;
        }
        if (page) {
          setRows(page.items);
          setTotal(page.total);
          setTruncated(page.truncated);
        } else if (options.patch) {
          // `before` is the list the student acted on; every action is disabled
          // while busy, so it cannot have changed since.
          const next = options.patch(before);
          setRows(next);
          setTotal((count) => count - (before.length - next.length));
        }
        applyShortlist(list.items);
        setSelected(new Set());
        setNotice(message);
        setUndoTarget(options.undo ?? null);
      })
      .catch(fail)
      .finally(() => {
        if (requestId.current === token) {
          setBusy(false);
        }
      });
  }

  function markStatus(
    targets: readonly ShortlistTarget[],
    status: MatchRow["shortlistStatus"],
  ): (current: readonly MatchRow[]) => readonly MatchRow[] {
    const keys = new Set(targets.map(keyOf));
    return (current) =>
      current.map((row) => (keys.has(keyOf(row.target)) ? { ...row, shortlistStatus: status } : row));
  }

  function restore(target: ShortlistTarget, name: string): void {
    change(() => removeFromShortlist(target), `${name} is back in your matches.`, { rerank: true });
  }

  function select(row: MatchRow, value: boolean): void {
    setSelected((current) => {
      const next = new Set(current);
      if (value) {
        if (next.size >= MAX_SELECTION) {
          return current;
        }
        next.add(keyOf(row.target));
      } else {
        next.delete(keyOf(row.target));
      }
      return next;
    });
  }

  function shortlistSelected(): void {
    const targets = rows.filter((row) => selected.has(keyOf(row.target))).map((row) => row.target);
    if (targets.length === 0) {
      return;
    }
    change(
      () => addToShortlist(targets),
      `Shortlisted ${targets.length} ${targets.length === 1 ? "entry" : "entries"}.`,
      { patch: markStatus(targets, "shortlisted") },
    );
  }

  function submitFilters(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    show(tab, { area: draft.area.trim(), country: draft.country.trim() });
  }

  if (state === "loading") {
    return <LoadingPanel message="Scoring cached records against your profile…" />;
  }
  if (state === "signed-out") {
    return <SignedOutPanel intro="Matches use your saved profile, so they need a signed-in account." />;
  }
  if (state === "no-profile") {
    return (
      <section className="panel" aria-labelledby="no-profile-title">
        <h2 id="no-profile-title">Save your profile first</h2>
        <p>
          Matches are scored against your research keywords, skills, degree, target countries and
          internship dates. Save a profile, then come back.
        </p>
        <a className="button" href="/onboarding">
          Go to your profile
        </a>
      </section>
    );
  }
  if (state === "failed") {
    return (
      <RetryPanel
        message={error ?? "Matches could not be loaded."}
        onRetry={() => {
          setState("loading");
          setError(null);
          setReloadToken((token) => token + 1);
        }}
      />
    );
  }

  const selectedCount = rows.filter((row) => selected.has(keyOf(row.target))).length;

  return (
    <div className="research-browser">
      <div className="button-row" role="tablist" aria-label="Match type">
        {(["faculty", "programs"] as const).map((value) => (
          <button
            key={value}
            type="button"
            role="tab"
            aria-selected={tab === value}
            className={`button ${tab === value ? "" : "button-quiet"}`}
            disabled={busy}
            onClick={() => show(value, filters)}
          >
            {value === "faculty" ? "Faculty" : "Programs"}
          </button>
        ))}
      </div>

      <form className="panel research-filters" aria-label="Match filters" onSubmit={submitFilters}>
        <div className="field-row">
          <div className="field">
            <label htmlFor="match-area">Research area</label>
            <input
              id="match-area"
              value={draft.area}
              placeholder="e.g. machine learning"
              onChange={(event) => setDraft((current) => ({ ...current, area: event.target.value }))}
            />
          </div>
          <div className="field">
            <label htmlFor="match-country">Country</label>
            <input
              id="match-country"
              value={draft.country}
              placeholder="Any country"
              onChange={(event) => setDraft((current) => ({ ...current, country: event.target.value }))}
            />
          </div>
        </div>
        <div className="button-row">
          <button type="submit" className="button" disabled={busy}>
            Apply filters
          </button>
        </div>
      </form>

      {error ? (
        <p className="alert" role="alert">
          {error}
        </p>
      ) : null}
      {notice ? (
        <p className="hint" role="status">
          {notice}{" "}
          {undoTarget ? (
            <button
              type="button"
              className="button button-quiet"
              disabled={busy}
              onClick={() => restore(undoTarget, "The entry")}
            >
              Undo
            </button>
          ) : null}
        </p>
      ) : null}

      <section aria-labelledby="matches-title">
        <div className="section-head">
          <h2 id="matches-title">{tab === "faculty" ? "Faculty matches" : "Program matches"}</h2>
          <p className="counts">
            {total} ranked · best first · ineligible last
          </p>
        </div>
        {truncated ? (
          <p className="hint">
            More records matched than one request scores. Narrow the filters to see the rest.
          </p>
        ) : null}
        <div className="button-row">
          <button
            type="button"
            className="button"
            disabled={busy || selectedCount === 0}
            onClick={shortlistSelected}
          >
            Shortlist selected ({selectedCount})
          </button>
        </div>
        {selectedCount >= MAX_SELECTION ? (
          <p className="hint">
            You can shortlist up to {MAX_SELECTION} at a time. Shortlist these, then select more.
          </p>
        ) : null}
        {rows.length === 0 ? (
          <p className="hint">
            Nothing to rank for these filters. The cache only holds records a source returned, and
            excluded entries are hidden.
          </p>
        ) : (
          <ul className="research-list" role="list">
            {rows.map((row) => (
              <MatchCard
                key={keyOf(row.target)}
                row={row}
                selected={selected.has(keyOf(row.target))}
                selectionFull={selectedCount >= MAX_SELECTION && !selected.has(keyOf(row.target))}
                busy={busy}
                onSelect={select}
                onShortlist={(item) =>
                  change(() => addToShortlist([item.target]), `Shortlisted ${item.name}.`, {
                    patch: markStatus([item.target], "shortlisted"),
                  })
                }
                onExclude={(item) =>
                  change(
                    () => excludeFromMatches([item.target]),
                    `${item.name} will no longer be suggested.`,
                    {
                      patch: (current) =>
                        current.filter((row) => keyOf(row.target) !== keyOf(item.target)),
                      undo: item.target,
                    },
                  )
                }
              />
            ))}
          </ul>
        )}
        {rows.length < total ? (
          <button type="button" className="button button-quiet" disabled={busy} onClick={loadMore}>
            Load more matches
          </button>
        ) : null}
      </section>

      <section aria-labelledby="shortlist-title">
        <div className="section-head">
          <h2 id="shortlist-title">My shortlist</h2>
          <p className="counts">
            {shortlist.length} {shortlist.length === 1 ? "entry" : "entries"} · only you can see it
          </p>
        </div>
        {shortlist.some((entry) => entry.current === false) ? (
          <button
            type="button"
            className="button button-quiet"
            disabled={busy}
            onClick={() => change(refreshShortlist, "Saved scores refreshed.")}
          >
            Refresh saved scores
          </button>
        ) : null}
        {shortlist.length === 0 ? (
          <p className="hint">Nothing shortlisted yet. Select matches above and shortlist them.</p>
        ) : (
          <ul className="research-list" role="list">
            {shortlist.map((entry) => (
              <ShortlistRow
                key={entry.id}
                entry={entry}
                busy={busy}
                onRemove={(item) => {
                  const target = { type: item.target_type as MatchTargetType, id: item.target_id };
                  change(
                    () => removeFromShortlist(target),
                    `Removed ${item.target_name ?? "the entry"} from your shortlist.`,
                    { patch: markStatus([target], null) },
                  );
                }}
              />
            ))}
          </ul>
        )}
      </section>

      <section aria-labelledby="excluded-title">
        <div className="section-head">
          <h2 id="excluded-title">Excluded</h2>
          <p className="counts">
            {excluded.length} hidden from your matches
          </p>
        </div>
        {excluded.length === 0 ? (
          <p className="hint">Nothing excluded. Exclude a match to stop it being suggested.</p>
        ) : (
          <ul className="research-list" role="list">
            {excluded.map((entry) => {
              const name = entry.target_name ?? "No longer in the research cache";
              return (
                <li className="card research-excluded" key={entry.id}>
                  <div className="card-head">
                    <h3>{name}</h3>
                  </div>
                  <p className="hint">
                    {entry.target_type === "faculty" ? "Faculty" : "Program"} · excluded{" "}
                    {formatDate(entry.updated_at)}
                  </p>
                  <button
                    type="button"
                    className="button button-quiet"
                    disabled={busy}
                    aria-label={`Restore ${name} to your matches`}
                    onClick={() =>
                      restore({ type: entry.target_type as MatchTargetType, id: entry.target_id }, name)
                    }
                  >
                    Restore
                  </button>
                </li>
              );
            })}
          </ul>
        )}
      </section>

      <p className="hint">
        Scores come from your saved profile and the shared research cache; no source is contacted
        here. A high score never hides a failed eligibility condition, and an unknown is listed
        rather than guessed.
      </p>
    </div>
  );
}
