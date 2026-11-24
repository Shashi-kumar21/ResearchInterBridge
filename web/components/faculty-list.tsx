"use client";

// Plain anchors are deliberate in this client component: they keep the screen
// renderable in tests without the App Router.

import { useEffect, useRef, useState } from "react";

import {
  ApiError,
  getResearchFaculty,
  listResearchFaculty,
  type ResearchFaculty,
  type ResearchFacultyDetail,
  type ResearchPublication,
  type ResearchPublicationDetail,
} from "@/lib/api";
import { describeApiError } from "@/lib/errors";
import { formatDate } from "@/lib/format";

/** Page size for the faculty list. The API caps a page at 100. */
const PAGE_SIZE = 10;

export interface FacultyScope {
  readonly institutionId?: number;
  readonly departmentId?: number;
  readonly area?: string;
  readonly country?: string;
}

/** A stable key for a scope. The parent remounts the list when it changes. */
export function facultyScopeKey(scope: FacultyScope): string {
  return JSON.stringify([
    scope.institutionId ?? null,
    scope.departmentId ?? null,
    scope.area ?? "",
    scope.country ?? "",
  ]);
}

interface FacultyListProps {
  /**
   * Cache filters. Render with `key={facultyScopeKey(scope)}` so a new scope
   * starts from a fresh first page instead of resetting state in an effect.
   */
  readonly scope: FacultyScope;
  /** Section heading, for example "Faculty at Example University". */
  readonly title: string;
  /** Called on a 401 so the parent can show its signed-out panel. */
  readonly onSessionLost: () => void;
}

function isSessionLost(failure: unknown): boolean {
  return failure instanceof ApiError && failure.status === 401;
}

function stated(value: string | null | undefined): string {
  return value && value.trim() ? value : "Not stated";
}

function External({ href, children }: { readonly href: string; readonly children: string }) {
  return (
    <a href={href} rel="noreferrer noopener" target="_blank">
      {children}
    </a>
  );
}

/**
 * The contact line. Only a verified address is ever rendered (ADR-021), and it
 * is plain text, not a mailto link: sending is a later, consent-gated phase,
 * and `contact_sendable` is the only flag that may unlock it.
 */
function ContactLine({ member }: { readonly member: ResearchFaculty }) {
  if (member.email_status === "verified" && member.email) {
    return (
      <p className="hint faculty-contact">
        Email: <span className="faculty-email">{member.email}</span> · verified on{" "}
        {member.email_source_url ? (
          <External href={member.email_source_url}>the official page</External>
        ) : (
          "the official page"
        )}
        {member.contact_sendable
          ? null
          : ". This record is stale, so the address is for orientation only until a source checks it again."}
      </p>
    );
  }
  if (member.email_status === "unverified") {
    return (
      <p className="hint faculty-contact">
        Email not verified: an address was found outside the official profile, so it is not
        shown.{" "}
        {member.email_source_url ? (
          <External href={member.email_source_url}>Where it was found</External>
        ) : null}
      </p>
    );
  }
  return <p className="hint faculty-contact">No email address is stated on the official page.</p>;
}

function PaperLine({ paper }: { readonly paper: ResearchPublication }) {
  return (
    <>
      <External href={paper.url}>{paper.title}</External>{" "}
      <span className="hint">
        · {paper.year ?? "Year not stated"}
        {paper.venue ? ` · ${paper.venue}` : ""}
        {paper.recent ? " · Recent" : ""}
      </span>
    </>
  );
}

function PaperDetails({ paper }: { readonly paper: ResearchPublicationDetail }) {
  return (
    <li>
      <PaperLine paper={paper} />
      <p className="hint">
        {paper.authors.length > 0
          ? paper.authors.join(", ")
          : "Author list not stored (missing or truncated at the source)."}
        {paper.doi ? ` · DOI ${paper.doi}` : ""}
      </p>
      {paper.abstract ? (
        <details>
          <summary>Abstract</summary>
          <p>{paper.abstract}</p>
        </details>
      ) : null}
    </li>
  );
}

interface FacultyCardProps {
  readonly member: ResearchFaculty;
  readonly expanded: boolean;
  readonly detail: ResearchFacultyDetail | undefined;
  readonly loadingDetail: boolean;
  readonly onToggle: (member: ResearchFaculty) => void;
}

function FacultyCard({ member, expanded, detail, loadingDetail, onToggle }: FacultyCardProps) {
  return (
    <li className="card research-faculty">
      <div className="card-head">
        <h3>{member.full_name}</h3>
        <span className={`badge ${member.stale ? "badge-planned" : "badge-available"}`}>
          {member.stale ? "Stale" : "Cached"}
        </span>
      </div>
      <p className="hint">
        {stated(member.title)} · {member.institution} · {stated(member.department)} ·{" "}
        {stated(member.country)}
      </p>
      <p className="research-meta">
        Research interests: {stated(member.research_interests.join(", "))}
      </p>
      <ContactLine member={member} />
      {member.highlighted_publications.length > 0 ? (
        <ul className="faculty-papers" aria-label={`Recent papers by ${member.full_name}`}>
          {member.highlighted_publications.map((paper) => (
            <li key={paper.id}>
              <PaperLine paper={paper} />
            </li>
          ))}
        </ul>
      ) : null}
      <p className="hint">
        {member.publication_count} {member.publication_count === 1 ? "paper" : "papers"}{" "}
        {member.orcid ? `linked by ORCID ${member.orcid}` : "linked (no ORCID iD stated)"} · Last
        checked {formatDate(member.last_checked_at)} ·{" "}
        <External href={member.profile_url}>Official profile</External>
      </p>
      <button
        type="button"
        className="button button-quiet"
        aria-expanded={expanded}
        aria-label={`Details for ${member.full_name}`}
        onClick={() => onToggle(member)}
      >
        {expanded ? "Hide details" : "Details"}
      </button>
      {expanded ? (
        loadingDetail && !detail ? (
          <p className="hint" aria-live="polite">
            Loading the latest cached copy…
          </p>
        ) : detail ? (
          <div className="research-detail">
            <dl>
              <dt>Lab</dt>
              <dd>{stated(detail.lab_name)}</dd>
              <dt>Recruiting evidence</dt>
              <dd>
                {detail.recruitment_evidence_url ? (
                  <External href={detail.recruitment_evidence_url}>Source page</External>
                ) : (
                  "Not stated"
                )}
              </dd>
            </dl>
            {detail.publications.length > 0 ? (
              <>
                <h4>All attributed papers ({detail.publications.length})</h4>
                <ul className="faculty-papers">
                  {detail.publications.map((paper) => (
                    <PaperDetails key={paper.id} paper={paper} />
                  ))}
                </ul>
              </>
            ) : (
              <p className="hint">No paper is attributed to this person in the cache.</p>
            )}
            {detail.evidence_urls.length > 0 ? (
              <details>
                <summary>All sources ({detail.evidence_urls.length})</summary>
                <ul>
                  {detail.evidence_urls.map((url) => (
                    <li key={url}>
                      <External href={url}>{url}</External>
                    </li>
                  ))}
                </ul>
              </details>
            ) : null}
          </div>
        ) : null
      ) : null}
      {member.stale ? (
        <p className="hint">
          This cache entry is stale. Use it for orientation only until a source checks it again;
          it is not verified for outreach.
        </p>
      ) : null}
    </li>
  );
}

/**
 * Faculty for the current browse level, read from `/api/research/faculty`.
 *
 * The screen cannot trigger a source fetch: live faculty discovery is an
 * operator CLI command (ADR-021).
 */
export function FacultyList({ scope, title, onSessionLost }: FacultyListProps) {
  const { institutionId, departmentId, area, country } = scope;
  const [faculty, setFaculty] = useState<readonly ResearchFaculty[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [expanded, setExpanded] = useState<number | null>(null);
  const [details, setDetails] = useState<Readonly<Record<number, ResearchFacultyDetail>>>({});
  const [loadingDetail, setLoadingDetail] = useState<number | null>(null);
  const requestId = useRef(0);
  const sessionLost = useRef(onSessionLost);

  useEffect(() => {
    sessionLost.current = onSessionLost;
  }, [onSessionLost]);

  function fail(failure: unknown): void {
    if (isSessionLost(failure)) {
      sessionLost.current();
      return;
    }
    setError(describeApiError(failure));
  }

  useEffect(() => {
    const token = requestId.current + 1;
    requestId.current = token;
    listResearchFaculty({
      institution_id: institutionId,
      department_id: departmentId,
      area: area || undefined,
      country: country || undefined,
      limit: PAGE_SIZE,
    })
      .then((page) => {
        if (requestId.current !== token) {
          return;
        }
        setFaculty(page.faculty);
        setTotal(page.total);
      })
      .catch((failure: unknown) => {
        if (requestId.current !== token) {
          return;
        }
        if (isSessionLost(failure)) {
          sessionLost.current();
          return;
        }
        setError(describeApiError(failure));
      })
      .finally(() => {
        if (requestId.current === token) {
          setLoading(false);
        }
      });
  }, [institutionId, departmentId, area, country]);

  function loadMore(): void {
    const token = requestId.current + 1;
    requestId.current = token;
    setLoading(true);
    listResearchFaculty({
      institution_id: institutionId,
      department_id: departmentId,
      area: area || undefined,
      country: country || undefined,
      limit: PAGE_SIZE,
      offset: faculty.length,
    })
      .then((page) => {
        if (requestId.current !== token) {
          return;
        }
        setFaculty((current) => [...current, ...page.faculty]);
        setTotal(page.total);
      })
      .catch(fail)
      .finally(() => {
        if (requestId.current === token) {
          setLoading(false);
        }
      });
  }

  function toggle(member: ResearchFaculty): void {
    if (expanded === member.id) {
      setExpanded(null);
      return;
    }
    setExpanded(member.id);
    if (details[member.id]) {
      return;
    }
    setLoadingDetail(member.id);
    getResearchFaculty(member.id)
      .then((fresh) => {
        setDetails((current) => ({ ...current, [fresh.id]: fresh }));
      })
      .catch(fail)
      .finally(() => {
        setLoadingDetail((current) => (current === member.id ? null : current));
      });
  }

  return (
    <section aria-labelledby="research-faculty-title">
      <div className="section-head">
        <h2 id="research-faculty-title">{title}</h2>
        <p className="counts">
          {total} faculty · cache only
        </p>
      </div>
      {error ? (
        <p className="alert" role="alert">
          {error}
        </p>
      ) : null}
      {loading && faculty.length === 0 ? (
        <p className="hint" aria-live="polite">
          Reading cached faculty…
        </p>
      ) : faculty.length === 0 && !error ? (
        <p className="hint">
          No cached faculty match this level. Faculty appear only after an operator reads an
          official faculty page; nobody is guessed from a name.
        </p>
      ) : (
        <ul className="research-list" role="list">
          {faculty.map((member) => (
            <FacultyCard
              key={member.id}
              member={member}
              expanded={expanded === member.id}
              detail={details[member.id]}
              loadingDetail={loadingDetail === member.id}
              onToggle={toggle}
            />
          ))}
        </ul>
      )}
      {faculty.length < total ? (
        <button type="button" className="button button-quiet" disabled={loading} onClick={loadMore}>
          Load more faculty
        </button>
      ) : null}
    </section>
  );
}
