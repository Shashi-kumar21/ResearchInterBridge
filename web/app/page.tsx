import {
  CAPABILITIES,
  LIMITS,
  WORKFLOW,
  summaryCounts,
  type Capability,
  type CapabilityStatus,
  type CapabilitySurface,
} from "@/lib/roadmap";

const REPOSITORY_URL = "https://github.com/Shashi-kumar21/ResearchInterBridge";

const STATUS_LABELS: Record<CapabilityStatus, string> = {
  available: "Working today",
  "in-progress": "In progress",
  planned: "Planned",
};

const SURFACE_LABELS: Record<CapabilitySurface, string> = {
  cli: "Command line",
  web: "Website",
};

function CapabilityCard({ capability }: { capability: Capability }) {
  return (
    <li className="card">
      <div className="card-head">
        <h3>{capability.title}</h3>
        <span className={`badge badge-${capability.status}`}>
          {STATUS_LABELS[capability.status]}
        </span>
      </div>
      <p>{capability.detail}</p>
      <p className="surface">Available on: {SURFACE_LABELS[capability.surface]}</p>
    </li>
  );
}

export default function HomePage() {
  const counts = summaryCounts();

  return (
    <>
      <header className="site-header">
        <span className="brand">ResearchInterBridge</span>
        <span className="phase">Phase 2 · no email can be sent yet</span>
      </header>

      <main id="main">
        <section className="hero" aria-labelledby="hero-title">
          <p className="eyebrow">Research internships abroad, without the spam</p>
          <h1 id="hero-title">
            Find the group you actually want to join. Write to one professor at a
            time.
          </h1>
          <p className="lead">
            ResearchInterBridge helps students discover research groups and
            professors abroad, keep the evidence behind every claim in their CV
            straight, and send a single approved email from their own Gmail
            account — never a bulk campaign.
          </p>
          <div className="hero-actions">
            <a className="button" href={REPOSITORY_URL}>
              Read the source on GitHub
            </a>
            <a className="button button-quiet" href="#status">
              What works today
            </a>
          </div>
          <p className="note">
            This is an early build. The backend runs from the command line and
            nothing in the repository can send email; accounts, profiles,
            documents and confirmed facts are real and tested.
          </p>
        </section>

        <section id="status" aria-labelledby="status-title">
          <div className="section-head">
            <h2 id="status-title">What works today</h2>
            <p className="counts">
              {counts.available} working · {counts.inProgress} in progress ·{" "}
              {counts.planned} planned
            </p>
          </div>
          <p className="section-lead">
            Every entry below is checked by the test suite against the code that
            exists. Nothing is labelled working until it is.
          </p>
          <ul className="grid">
            {CAPABILITIES.map((capability) => (
              <CapabilityCard key={capability.id} capability={capability} />
            ))}
          </ul>
        </section>

        <section aria-labelledby="workflow-title">
          <h2 id="workflow-title">How it will work</h2>
          <ol className="steps">
            {WORKFLOW.map((step) => (
              <li key={step.id}>
                <h3>{step.title}</h3>
                <p>{step.detail}</p>
              </li>
            ))}
          </ol>
        </section>

        <section aria-labelledby="limits-title">
          <h2 id="limits-title">Privacy, fairness and limits</h2>
          <ul className="grid">
            {LIMITS.map((limit) => (
              <li className="card" key={limit.id}>
                <h3>{limit.title}</h3>
                <p>{limit.detail}</p>
              </li>
            ))}
          </ul>
        </section>
      </main>

      <footer className="site-footer">
        <p>
          Free and open source. Product specification, decisions and the audit
          live in the{" "}
          <a href={`${REPOSITORY_URL}/blob/dev/task.md`}>specification</a> and{" "}
          <a href={`${REPOSITORY_URL}/blob/dev/docs/decisions.md`}>decision log</a>.
        </p>
        <p className="fine-print">
          Sending is authorized by Google&apos;s authenticated sending from your
          own account, not by this website. No result is guaranteed, and no
          contact, paper or deadline is ever invented.
        </p>
      </footer>
    </>
  );
}
