"use client";

// Plain anchors are deliberate in this client component: they keep the screen
// renderable in tests without the App Router.

import { useEffect, useRef, useState, type FormEvent } from "react";

import {
  ApiError,
  getResearchProgram,
  listInstitutionDepartments,
  listResearchInstitutions,
  listResearchPrograms,
  type ResearchDepartment,
  type ResearchInstitution,
  type ResearchProgram,
  type ResearchProgramFilters,
} from "@/lib/api";
import { describeApiError } from "@/lib/errors";
import { formatDate } from "@/lib/format";

import { LoadingPanel, RetryPanel, SignedOutPanel } from "./account-panels";

type LoadState = "loading" | "ready" | "signed-out" | "failed";

/** Page size for each browse level. The API caps a page at 100. */
const PAGE_SIZE = 10;

interface AppliedFilters {
  readonly country: string;
  readonly degreeLevel: string;
  readonly area: string;
}

const NO_FILTERS: AppliedFilters = { country: "", degreeLevel: "", area: "" };

const DEGREE_OPTIONS: readonly { readonly value: string; readonly label: string }[] = [
  { value: "", label: "Any degree level" },
  { value: "bachelor", label: "Bachelor" },
  { value: "master", label: "Master" },
  { value: "phd", label: "PhD" },
];

function isSessionLost(failure: unknown): boolean {
  return failure instanceof ApiError && failure.status === 401;
}

function stated(value: string | null | undefined): string {
  return value && value.trim() ? value : "Not stated";
}

function statusLabel(status: string): string {
  switch (status) {
    case "open":
      return "Open";
    case "upcoming":
      return "Upcoming";
    case "closed":
      return "Closed";
    case "unverified":
      return "Not verified";
    default:
      return status;
  }
}

function degreeLabel(value: string | null): string {
  if (!value) {
    return "Not stated";
  }
  return value.charAt(0).toUpperCase() + value.slice(1);
}

function filtersFromProgram(filters: AppliedFilters): ResearchProgramFilters {
  return {
    area: filters.area || undefined,
    degree_level: filters.degreeLevel || undefined,
    limit: PAGE_SIZE,
  };
}

interface FactRowProps {
  readonly label: string;
  readonly value: string;
}

function FactRow({ label, value }: FactRowProps) {
  return (
    <>
      <dt>{label}</dt>
      <dd>{value}</dd>
    </>
  );
}

function ProgramDetails({ program }: { readonly program: ResearchProgram }) {
  return (
    <div className="research-detail">
      <dl>
        <FactRow label="Opportunity type" value={stated(program.opportunity_type)} />
        <FactRow label="Degree level" value={degreeLabel(program.degree_level)} />
        <FactRow label="Research areas" value={stated(program.research_areas.join(", "))} />
        <FactRow label="Eligibility" value={stated(program.eligibility.join(" · "))} />
        <FactRow label="Application method" value={stated(program.application_method)} />
        <FactRow label="Opens" value={formatDate(program.opening_date)} />
        <FactRow label="Closes" value={formatDate(program.closing_date)} />
        <FactRow label="Starts" value={formatDate(program.start_date)} />
        <FactRow label="Duration" value={stated(program.duration)} />
        <FactRow label="Stipend" value={stated(program.stipend)} />
        <FactRow label="Tuition" value={stated(program.tuition)} />
        <FactRow label="Accommodation" value={stated(program.accommodation)} />
        <FactRow
          label="Required documents"
          value={stated(program.required_documents.join(" · "))}
        />
        <FactRow
          label="Confidence"
          value={
            program.confidence === null
              ? "Not scored"
              : `${Math.round(program.confidence * 100)}%`
          }
        />
      </dl>
      <p className="hint">
        Status: {statusLabel(program.status)} · Last checked{" "}
        {formatDate(program.last_checked_at)} ·{" "}
        <a href={program.official_url} rel="noreferrer noopener" target="_blank">
          Official page
        </a>
      </p>
      {program.evidence_urls.length > 1 ? (
        <details>
          <summary>All sources ({program.evidence_urls.length})</summary>
          <ul>
            {program.evidence_urls.map((url) => (
              <li key={url}>
                <a href={url} rel="noreferrer noopener" target="_blank">
                  {url}
                </a>
              </li>
            ))}
          </ul>
        </details>
      ) : null}
      {program.stale ? (
        <p className="hint">
          This cache entry is stale. Use it for orientation only until a source checks it again;
          do not treat it as verified for outreach or a deadline decision.
        </p>
      ) : null}
    </div>
  );
}

interface ProgramCardProps {
  readonly program: ResearchProgram;
  readonly expanded: boolean;
  readonly detail: ResearchProgram | undefined;
  readonly loadingDetail: boolean;
  readonly onToggle: (program: ResearchProgram) => void;
}

function ProgramCard({
  program,
  expanded,
  detail,
  loadingDetail,
  onToggle,
}: ProgramCardProps) {
  const shown = detail ?? program;
  return (
    <li className="card research-program">
      <div className="card-head">
        <h3>{program.name}</h3>
        <span className={`badge ${program.stale ? "badge-planned" : "badge-available"}`}>
          {program.stale ? "Stale" : "Cached"}
        </span>
      </div>
      <p className="hint">
        {stated(program.institution)} · {stated(program.department)} ·{" "}
        {stated(program.country)}
      </p>
      <p className="research-meta">
        {degreeLabel(program.degree_level)} · {statusLabel(program.status)} · Closes{" "}
        {formatDate(program.closing_date)}
      </p>
      <p className="hint">
        Last checked {formatDate(program.last_checked_at)} ·{" "}
        <a href={program.official_url} rel="noreferrer noopener" target="_blank">
          Official page
        </a>
      </p>
      <button
        type="button"
        className="button button-quiet"
        aria-expanded={expanded}
        aria-label={`Details for ${program.name}`}
        onClick={() => onToggle(program)}
      >
        {expanded ? "Hide details" : "Details"}
      </button>
      {expanded ? (
        loadingDetail && !detail ? (
          <p className="hint" aria-live="polite">
            Loading the latest cached copy…
          </p>
        ) : (
          <ProgramDetails program={shown} />
        )
      ) : null}
    </li>
  );
}

/**
 * University → department → program browsing over the shared cache (§13.2).
 *
 * Every request is a read against `/api/research/*`; this screen has no way to
 * trigger a source fetch. Rows keep their `last_checked` date and a stale badge,
 * and an unstated field renders as "Not stated" instead of being filled in.
 */
export function ResearchBrowser() {
  const [state, setState] = useState<LoadState>("loading");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [draft, setDraft] = useState<AppliedFilters>(NO_FILTERS);
  const [filters, setFilters] = useState<AppliedFilters>(NO_FILTERS);

  const [institutions, setInstitutions] = useState<readonly ResearchInstitution[]>([]);
  const [institutionTotal, setInstitutionTotal] = useState(0);
  const [institution, setInstitution] = useState<ResearchInstitution | null>(null);

  const [departments, setDepartments] = useState<readonly ResearchDepartment[]>([]);
  const [departmentTotal, setDepartmentTotal] = useState(0);
  const [department, setDepartment] = useState<ResearchDepartment | null>(null);

  const [programs, setPrograms] = useState<readonly ResearchProgram[]>([]);
  const [programTotal, setProgramTotal] = useState(0);

  const [expanded, setExpanded] = useState<number | null>(null);
  const [details, setDetails] = useState<Readonly<Record<number, ResearchProgram>>>({});
  const [loadingDetail, setLoadingDetail] = useState<number | null>(null);

  const [reloadToken, setReloadToken] = useState(0);
  const requestId = useRef(0);

  useEffect(() => {
    let cancelled = false;
    Promise.all([
      listResearchInstitutions({ limit: PAGE_SIZE }),
      listResearchPrograms({ limit: PAGE_SIZE }),
    ])
      .then(([institutionPage, programPage]) => {
        if (cancelled) {
          return;
        }
        setInstitutions(institutionPage.institutions);
        setInstitutionTotal(institutionPage.total);
        setPrograms(programPage.programs);
        setProgramTotal(programPage.total);
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
    setState("loading");
    setError(null);
    setInstitutions([]);
    setInstitutionTotal(0);
    setInstitution(null);
    setDepartments([]);
    setDepartmentTotal(0);
    setDepartment(null);
    setPrograms([]);
    setProgramTotal(0);
    setExpanded(null);
    setDetails({});
    setReloadToken((token) => token + 1);
  }

  function handleFailure(failure: unknown): void {
    if (isSessionLost(failure)) {
      setState("signed-out");
      return;
    }
    setError(describeApiError(failure));
  }

  function applyFilters(next: AppliedFilters): void {
    const token = requestId.current + 1;
    requestId.current = token;
    setFilters(next);
    setInstitution(null);
    setDepartment(null);
    setDepartments([]);
    setDepartmentTotal(0);
    setExpanded(null);
    setBusy(true);
    setError(null);
    Promise.all([
      listResearchInstitutions({
        country: next.country || undefined,
        limit: PAGE_SIZE,
      }),
      listResearchPrograms({
        area: next.area || undefined,
        degree_level: next.degreeLevel || undefined,
        limit: PAGE_SIZE,
      }),
    ])
      .then(([institutionPage, programPage]) => {
        if (requestId.current !== token) {
          return;
        }
        setInstitutions(institutionPage.institutions);
        setInstitutionTotal(institutionPage.total);
        setPrograms(programPage.programs);
        setProgramTotal(programPage.total);
      })
      .catch(handleFailure)
      .finally(() => {
        if (requestId.current === token) {
          setBusy(false);
        }
      });
  }

  function submitFilters(event: FormEvent<HTMLFormElement>): void {
    event.preventDefault();
    applyFilters({
      country: draft.country.trim(),
      degreeLevel: draft.degreeLevel,
      area: draft.area.trim(),
    });
  }

  function resetFilters(): void {
    setDraft(NO_FILTERS);
    applyFilters(NO_FILTERS);
  }

  function openInstitution(next: ResearchInstitution): void {
    const token = requestId.current + 1;
    requestId.current = token;
    setInstitution(next);
    setDepartment(null);
    setExpanded(null);
    setBusy(true);
    setError(null);
    Promise.all([
      listInstitutionDepartments(next.id, { limit: PAGE_SIZE }),
      listResearchPrograms({
        ...filtersFromProgram(filters),
        institution_id: next.id,
      }),
    ])
      .then(([departmentPage, programPage]) => {
        if (requestId.current !== token) {
          return;
        }
        setDepartments(departmentPage.departments);
        setDepartmentTotal(departmentPage.total);
        setPrograms(programPage.programs);
        setProgramTotal(programPage.total);
      })
      .catch(handleFailure)
      .finally(() => {
        if (requestId.current === token) {
          setBusy(false);
        }
      });
  }

  function chooseDepartment(next: ResearchDepartment): void {
    const token = requestId.current + 1;
    requestId.current = token;
    setDepartment(next);
    setExpanded(null);
    setBusy(true);
    setError(null);
    listResearchPrograms({
      area: filters.area || undefined,
      degree_level: filters.degreeLevel || undefined,
      department_id: next.id,
      limit: PAGE_SIZE,
    })
      .then((programPage) => {
        if (requestId.current !== token) {
          return;
        }
        setPrograms(programPage.programs);
        setProgramTotal(programPage.total);
      })
      .catch(handleFailure)
      .finally(() => {
        if (requestId.current === token) {
          setBusy(false);
        }
      });
  }

  function showWholeInstitution(): void {
    if (!institution) {
      return;
    }
    const token = requestId.current + 1;
    requestId.current = token;
    setDepartment(null);
    setExpanded(null);
    setBusy(true);
    setError(null);
    listResearchPrograms({
      ...filtersFromProgram(filters),
      institution_id: institution.id,
    })
      .then((programPage) => {
        if (requestId.current !== token) {
          return;
        }
        setPrograms(programPage.programs);
        setProgramTotal(programPage.total);
      })
      .catch(handleFailure)
      .finally(() => {
        if (requestId.current === token) {
          setBusy(false);
        }
      });
  }

  function loadMoreInstitutions(): void {
    const token = requestId.current + 1;
    requestId.current = token;
    setBusy(true);
    listResearchInstitutions({
      country: filters.country || undefined,
      limit: PAGE_SIZE,
      offset: institutions.length,
    })
      .then((page) => {
        if (requestId.current !== token) {
          return;
        }
        setInstitutions((current) => [...current, ...page.institutions]);
        setInstitutionTotal(page.total);
      })
      .catch(handleFailure)
      .finally(() => {
        if (requestId.current === token) {
          setBusy(false);
        }
      });
  }

  function loadMoreDepartments(): void {
    if (!institution) {
      return;
    }
    const token = requestId.current + 1;
    requestId.current = token;
    setBusy(true);
    listInstitutionDepartments(institution.id, {
      limit: PAGE_SIZE,
      offset: departments.length,
    })
      .then((page) => {
        if (requestId.current !== token) {
          return;
        }
        setDepartments((current) => [...current, ...page.departments]);
        setDepartmentTotal(page.total);
      })
      .catch(handleFailure)
      .finally(() => {
        if (requestId.current === token) {
          setBusy(false);
        }
      });
  }

  function loadMorePrograms(): void {
    const token = requestId.current + 1;
    requestId.current = token;
    setBusy(true);
    listResearchPrograms({
      area: filters.area || undefined,
      degree_level: filters.degreeLevel || undefined,
      institution_id: department ? undefined : institution?.id,
      department_id: department?.id,
      limit: PAGE_SIZE,
      offset: programs.length,
    })
      .then((page) => {
        if (requestId.current !== token) {
          return;
        }
        setPrograms((current) => [...current, ...page.programs]);
        setProgramTotal(page.total);
      })
      .catch(handleFailure)
      .finally(() => {
        if (requestId.current === token) {
          setBusy(false);
        }
      });
  }

  function toggleDetails(program: ResearchProgram): void {
    if (expanded === program.id) {
      setExpanded(null);
      return;
    }
    setExpanded(program.id);
    if (details[program.id]) {
      return;
    }
    setLoadingDetail(program.id);
    getResearchProgram(program.id)
      .then((fresh) => {
        setDetails((current) => ({ ...current, [fresh.id]: fresh }));
      })
      .catch(handleFailure)
      .finally(() => {
        setLoadingDetail((current) => (current === program.id ? null : current));
      });
  }

  if (state === "loading") {
    return <LoadingPanel message="Reading the shared research cache…" />;
  }

  if (state === "signed-out") {
    return (
      <SignedOutPanel intro="Research browsing reads the shared cache, so it needs a signed-in account." />
    );
  }

  if (state === "failed") {
    return <RetryPanel message={error ?? "The research cache could not be read."} onRetry={retry} />;
  }

  const scope = department
    ? `Programs in ${department.name}`
    : institution
      ? `Programs at ${institution.name}`
      : "Programs in the shared cache";

  return (
    <div className="research-browser">
      <form className="panel research-filters" aria-label="Research filters" onSubmit={submitFilters}>
        <div className="field-row">
          <div className="field">
            <label htmlFor="research-country">Country</label>
            <input
              id="research-country"
              name="country"
              value={draft.country}
              placeholder="Any country"
              onChange={(event) => setDraft((current) => ({ ...current, country: event.target.value }))}
            />
          </div>
          <div className="field">
            <label htmlFor="research-degree">Degree level</label>
            <select
              id="research-degree"
              name="degree_level"
              value={draft.degreeLevel}
              onChange={(event) =>
                setDraft((current) => ({ ...current, degreeLevel: event.target.value }))
              }
            >
              {DEGREE_OPTIONS.map((option) => (
                <option key={option.value} value={option.value}>
                  {option.label}
                </option>
              ))}
            </select>
          </div>
          <div className="field">
            <label htmlFor="research-area">Research area</label>
            <input
              id="research-area"
              name="area"
              value={draft.area}
              placeholder="e.g. machine learning"
              onChange={(event) => setDraft((current) => ({ ...current, area: event.target.value }))}
            />
          </div>
        </div>
        <p className="hint">
          Filters only match values a source stated. A blank field in a cached record never
          matches a filter.
        </p>
        <div className="button-row">
          <button type="submit" className="button" disabled={busy}>
            Apply filters
          </button>
          <button
            type="button"
            className="button button-quiet"
            disabled={busy}
            onClick={resetFilters}
          >
            Reset
          </button>
        </div>
      </form>

      {error ? (
        <p className="alert" role="alert">
          {error}
        </p>
      ) : null}
      {busy ? (
        <p className="hint" aria-live="polite">
          Reading the cache…
        </p>
      ) : null}

      <nav className="research-trail" aria-label="Browse level">
        <button
          type="button"
          className="button button-quiet"
          aria-current={institution === null ? "page" : undefined}
          onClick={() => applyFilters(filters)}
        >
          All institutions
        </button>
        {institution ? (
          <>
            <span aria-hidden="true">/</span>
            <button
              type="button"
              className="button button-quiet"
              aria-current={department === null ? "page" : undefined}
              onClick={showWholeInstitution}
            >
              {institution.name}
            </button>
          </>
        ) : null}
        {department ? (
          <>
            <span aria-hidden="true">/</span>
            <span>{department.name}</span>
          </>
        ) : null}
      </nav>

      <section aria-labelledby="research-institutions-title">
        <div className="section-head">
          <h2 id="research-institutions-title">Institutions</h2>
          <p className="counts">
            {institutionTotal} {institutionTotal === 1 ? "match" : "matches"}
          </p>
        </div>
        {institutions.length === 0 ? (
          <p className="hint">
            No institution matches these filters. The cache only holds records a source has
            returned; nothing is invented.
          </p>
        ) : (
          <ul className="research-list" role="list">
            {institutions.map((item) => (
              <li className="card research-institution" key={item.id}>
                <div className="card-head">
                  <h3>{item.name}</h3>
                  <span className={`badge ${item.stale ? "badge-planned" : "badge-available"}`}>
                    {item.stale ? "Stale" : "Cached"}
                  </span>
                </div>
                <p className="hint">
                  {stated(item.country)} · {item.program_count}{" "}
                  {item.program_count === 1 ? "program" : "programs"} · {item.department_count}{" "}
                  {item.department_count === 1 ? "department" : "departments"}
                </p>
                <p className="hint">Last checked {formatDate(item.last_checked_at)}</p>
                <button
                  type="button"
                  className="button button-quiet"
                  aria-label={`Browse ${item.name}`}
                  aria-pressed={institution?.id === item.id}
                  onClick={() => openInstitution(item)}
                >
                  Open
                </button>
              </li>
            ))}
          </ul>
        )}
        {institutions.length < institutionTotal ? (
          <button type="button" className="button button-quiet" disabled={busy} onClick={loadMoreInstitutions}>
            Load more institutions
          </button>
        ) : null}
      </section>

      {institution ? (
        <section aria-labelledby="research-departments-title">
          <div className="section-head">
            <h2 id="research-departments-title">Departments</h2>
            <p className="counts">
              {departmentTotal} {departmentTotal === 1 ? "match" : "matches"}
            </p>
          </div>
          {departments.length === 0 ? (
            <p className="hint">
              No department is cached for this institution yet. Programs still appear below.
            </p>
          ) : (
            <ul className="research-list" role="list">
              {departments.map((item) => (
                <li className="card research-department" key={item.id}>
                  <div>
                    <h3>{item.name}</h3>
                    <p className="hint">
                      {item.program_count} {item.program_count === 1 ? "program" : "programs"} ·
                      last checked {formatDate(item.last_checked_at)}
                    </p>
                    {item.stale ? <p className="hint">Stale: orientation only.</p> : null}
                  </div>
                  <button
                    type="button"
                    className="button button-quiet"
                    aria-label={`Filter programs by ${item.name}`}
                    aria-pressed={department?.id === item.id}
                    onClick={() => chooseDepartment(item)}
                  >
                    {department?.id === item.id ? "Selected" : "Open"}
                  </button>
                </li>
              ))}
            </ul>
          )}
          {departments.length < departmentTotal ? (
            <button type="button" className="button button-quiet" disabled={busy} onClick={loadMoreDepartments}>
              Load more departments
            </button>
          ) : null}
        </section>
      ) : null}

      <section aria-labelledby="research-programs-title">
        <div className="section-head">
          <h2 id="research-programs-title">{scope}</h2>
          <p className="counts">
            {programTotal} {programTotal === 1 ? "program" : "programs"} · cache only
          </p>
        </div>
        {programs.length === 0 ? (
          <p className="hint">
            No cached program matches these filters. Discovery does not invent a program to fill
            the page.
          </p>
        ) : (
          <ul className="research-list" role="list">
            {programs.map((program) => (
              <ProgramCard
                key={program.id}
                program={program}
                expanded={expanded === program.id}
                detail={details[program.id]}
                loadingDetail={loadingDetail === program.id}
                onToggle={toggleDetails}
              />
            ))}
          </ul>
        )}
        {programs.length < programTotal ? (
          <button type="button" className="button button-quiet" disabled={busy} onClick={loadMorePrograms}>
            Load more programs
          </button>
        ) : null}
      </section>

      <p className="hint">
        This screen reads the shared research cache only; no source is contacted here. A stale
        row may help you orient, but it is not verified for outreach or a deadline decision.
      </p>
    </div>
  );
}
