import { memo, useState } from "react";
import { Bell, BellRing, ChartCandlestick, Database, HeartPulse, KeyRound, Radar, ScanSearch, Activity } from "lucide-react";

import { LEARN_ELSEWHERE, LEARN_GLOSSARY, LEARN_SECTIONS, LEARN_SETUP_SECTIONS, LEARN_STEPS } from "./learnContent.js";
import { isScreenshotReady, screenshotById } from "./learnScreenshots.js";

// learnContent.js names icons as plain strings so node --test can import it.
// This map is the only place those names become components. An unknown name
// falls back rather than throwing: a missing lucide import does not fail the
// build here, it throws at render time and blanks the page.
const ICONS = {
  chart: ChartCandlestick,
  indicators: Activity,
  options: Database,
  alerts: Bell,
  scanner: ScanSearch,
  momx: Radar,
  "momo-alert": BellRing,
  "own-keys": KeyRound,
  health: HeartPulse,
};

function SectionIcon({ name }) {
  const Icon = ICONS[name] || Activity;
  return <Icon size={17} strokeWidth={1.8} />;
}

function GoThere({ goTo, label, onNavigate }) {
  if (!goTo) return null;
  return (
    <button className="learn-goto" type="button" onClick={() => onNavigate?.(goTo)}>
      {label}
    </button>
  );
}

function Shot({ id }) {
  if (!id || !isScreenshotReady(id)) return null;
  const shot = screenshotById(id);
  if (!shot) return null;
  const captured = new Date(shot.capturedOn);
  const stamp = Number.isNaN(captured.getTime())
    ? ""
    : captured.toLocaleDateString("en-US", { month: "long", year: "numeric" });
  return (
    <figure className="learn-shot">
      <div className="learn-shot-frame">
        <img
          src={`/learn/${shot.file}`}
          alt={shot.alt}
          width={shot.width}
          height={shot.height}
          loading="lazy"
          decoding="async"
        />
        {(shot.callouts || []).map((callout) => (
          <span
            className="learn-shot-marker"
            key={callout.n}
            style={{ left: `${callout.x}%`, top: `${callout.y}%` }}
            aria-hidden="true"
          >{callout.n}</span>
        ))}
      </div>
      {(shot.callouts || []).length ? (
        <figcaption>
          <ol className="learn-shot-legend">
            {shot.callouts.map((callout) => (
              <li key={callout.n}><b>{callout.n}</b><span>{callout.text}</span></li>
            ))}
          </ol>
          {stamp ? <small className="learn-shot-stamp">Captured {stamp}</small> : null}
        </figcaption>
      ) : null}
    </figure>
  );
}

// Real websites in the copy become tappable links (trader, 2026-08-30: "see
// the url for developer.schwab.com" - retyping an address on a phone is the
// kind of step a kid fumbles). DELIBERATELY absent: https://127.0.0.1/ - that
// address is meant to be TYPED INTO A FORM on Schwab's site, and tapping it
// would "helpfully" open a broken page mid-setup. Only destinations you are
// meant to VISIT are links.
const LEARN_LINKS = {
  "developer.schwab.com": "https://developer.schwab.com",
  "app.alpaca.markets": "https://app.alpaca.markets",
};
const LEARN_LINK_SPLIT = new RegExp(`(${Object.keys(LEARN_LINKS).map((d) => d.replace(/[.]/g, "[.]")).join("|")})`);

function linkify(text) {
  const parts = String(text).split(LEARN_LINK_SPLIT);
  if (parts.length === 1) return text;
  return parts.map((part, index) =>
    LEARN_LINKS[part] ? (
      <a
        key={index}
        className="learn-link"
        href={LEARN_LINKS[part]}
        target="_blank"
        rel="noopener noreferrer"
      >{part}</a>
    ) : (
      part
    ),
  );
}

function Block({ block }) {
  return (
    <div className="learn-block">
      <h4>{block.heading}</h4>
      {block.body ? block.body.map((paragraph, index) => <p key={index}>{linkify(paragraph)}</p>) : null}
      {block.rows ? (
        <dl className="learn-defs">
          {block.rows.map((row) => (
            <div key={row.term}><dt>{row.term}</dt><dd>{linkify(row.meaning)}</dd></div>
          ))}
        </dl>
      ) : null}
      {block.chips ? (
        <div className="learn-chips">
          {block.chips.map((chip) => (
            <span className={`learn-chip learn-chip-${chip.tone}`} key={chip.label}>
              <i />{chip.label}
            </span>
          ))}
        </div>
      ) : null}
    </div>
  );
}

// The Setup tab renders its sections always open (no <details>): the whole
// point of the tab is that every first-time-setup step is visible in one place.
function SetupTab() {
  return (
    <>
      <header className="learn-hero">
        <h1>Set up AGX</h1>
        <p>
          Everything you switch on once and then stop thinking about: the Momo Alert on
          this screen and on your phone, and - if you want it - your own data connections.
          At the bottom: how to check the app's health when something looks down.
        </p>
      </header>
      <section className="learn-reference" aria-labelledby="learn-setup-heading">
        <h2 id="learn-setup-heading">First-time setup</h2>
        {LEARN_SETUP_SECTIONS.map((section) => (
          <section className="learn-section learn-setup-section" key={section.id}>
            <h3 className="learn-setup-title">
              <SectionIcon name={section.icon} />
              <span>{section.title}</span>
            </h3>
            <div className="learn-section-body">
              {(section.concept || []).map((paragraph, index) => (
                <p className="learn-concept" key={index}>{paragraph}</p>
              ))}
              <Shot id={section.screenshot} />
              {section.blocks.map((block) => <Block block={block} key={block.heading} />)}
            </div>
          </section>
        ))}
      </section>
    </>
  );
}

const LEARN_TABS = [
  { id: "learn", label: "Learn" },
  { id: "setup", label: "Setup" },
];

// One tab bar, rendered from the list. It used to be copy-pasted per branch,
// which is how a third tab turns into a third copy that drifts from the other
// two.
function LearnTabs({ tab, setTab }) {
  return (
    <div className="learn-tabs" role="tablist" aria-label="Learn and Setup">
      {LEARN_TABS.map((entry) => (
        <button
          key={entry.id}
          className={entry.id === tab ? "learn-tab learn-tab-active" : "learn-tab"}
          type="button"
          role="tab"
          aria-selected={entry.id === tab}
          onClick={() => setTab(entry.id)}
        >
          {entry.label}
        </button>
      ))}
    </div>
  );
}

function LearningCenter({ onNavigate }) {
  // Component state only, on purpose: the tab is not a route, and Learn stays
  // the default so a brand-new user still lands on the walkthrough.
  const [tab, setTab] = useState("learn");
  if (tab === "setup") {
    return (
      <div className="learn-page">
        <LearnTabs tab={tab} setTab={setTab} />
        <SetupTab />
      </div>
    );
  }
  return (
    <div className="learn-page">
      <LearnTabs tab={tab} setTab={setTab} />
      <header className="learn-hero">
        <h1>Welcome to AGX</h1>
        <p>
          AGX finds stocks that are moving, shows you what the chart and the option chain
          say about them, and watches the levels you care about so you do not have to.
        </p>
        <p className="learn-hero-note">
          Work through the eight steps below on your first morning. Everything after them is
          reference you can come back to.
        </p>
      </header>

      <section className="learn-steps" aria-labelledby="learn-steps-heading">
        <h2 id="learn-steps-heading">Your first 15 minutes</h2>
        {LEARN_STEPS.map((step) => (
          <article className="learn-step" key={step.id}>
            <span className="learn-step-number" aria-hidden="true">{step.number}</span>
            <div>
              <h3>{step.title}</h3>
              {step.body.map((paragraph, index) => <p key={index}>{paragraph}</p>)}
              <Shot id={step.screenshot} />
              <GoThere goTo={step.goTo} label={step.goToLabel} onNavigate={onNavigate} />
            </div>
          </article>
        ))}
      </section>

      <section className="learn-reference" aria-labelledby="learn-reference-heading">
        <h2 id="learn-reference-heading">How each part works</h2>
        {LEARN_SECTIONS.map((section) => (
          <details className="learn-section" key={section.id}>
            <summary>
              <SectionIcon name={section.icon} />
              <span>{section.title}</span>
            </summary>
            <div className="learn-section-body">
              {section.concept.map((paragraph, index) => (
                <p className="learn-concept" key={index}>{paragraph}</p>
              ))}
              <Shot id={section.screenshot} />
              {section.blocks.map((block) => <Block block={block} key={block.heading} />)}
              <GoThere goTo={section.goTo} label={section.goToLabel} onNavigate={onNavigate} />
            </div>
          </details>
        ))}
      </section>

      <section className="learn-glossary" aria-labelledby="learn-glossary-heading">
        <h2 id="learn-glossary-heading">Words you will see</h2>
        <dl className="learn-defs">
          {LEARN_GLOSSARY.map((entry) => (
            <div key={entry.id}><dt>{entry.term}</dt><dd>{entry.plain}</dd></div>
          ))}
        </dl>
      </section>

      <section className="learn-elsewhere" aria-labelledby="learn-elsewhere-heading">
        <h2 id="learn-elsewhere-heading">Everything else</h2>
        <ul>
          {LEARN_ELSEWHERE.map((entry) => (
            <li key={entry.goTo}>
              <button type="button" onClick={() => onNavigate?.(entry.goTo)}>{entry.goToLabel}</button>
              <span>{entry.oneLiner}</span>
            </li>
          ))}
        </ul>
      </section>
    </div>
  );
}

// Static content with one prop. It must never re-render on a dashboard poll.
export default memo(LearningCenter);
