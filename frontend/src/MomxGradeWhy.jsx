import { useEffect } from "react";
import { createPortal } from "react-dom";

import {
  GRADE_DISCLAIMER,
  MOMENTUM_LABEL,
  MOMENTUM_LABEL_BEAR,
  PATTERN_ICON,
  adxText,
  chartLabel,
  hlDegreeText,
  sessionLabel,
  timelineDetail,
  trackRecordLine,
} from "./momxGrade.js";
import { snapshotTimeLabel } from "./momxHistory.js";

// The scanner grade "why" panel: opened by clicking a Setup cell on the Live
// or History board. It explains what the row's grade is made of, straight from
// the fields the worker attached (row.grade / row.m5 / row.gradeFresh - see
// docs/superpowers/specs/2026-09-21-momx-setup-grade-design.md, "Scanner").
// Nothing here recomputes the rule; it only reads it back.
//
// Uses the FILTERS dialog's overlay / head / body / foot classes, so it has the
// same phone-safe scroll shape (one scroller, no sticky inside it).
//
// MUST NEVER THROW: a History row may be months old and carry none of these
// fields, and a live row may be graded "None" after a grading exception.

// The eight SKIT timeframes the grade reads, in board order (momx/grade.py
// SKIT_TFS). Labels are the board's short forms.
const SKIT_TFS = ["2h", "4h", "D", "2D", "3D", "4D", "Wk", "M"];
const SQZ_TFS = ["4h", "D", "Wk"];
// The timeframes momx/columns.py ADX_TIMEFRAMES records.
const ADX_TFS = ["5m", "30m"];
const TF_LABEL = { Wk: "W", M: "Mo" };
const tfLabel = (tf) => TF_LABEL[tf] || tf;

const LETTERS = ["A+", "A", "B"];
const PATTERN_NAME = { explosive: "Explosive", steady: "Steady" };

function obj(value) {
  return value && typeof value === "object" && !Array.isArray(value) ? value : null;
}

function list(value) {
  return Array.isArray(value) ? value : [];
}

function finite(value) {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function money(value) {
  const number = finite(value);
  return number === null ? "–" : "$" + number.toFixed(2);
}

function cellOf(row, section, tf) {
  const group = obj(row && row[section]);
  return obj(group && group[tf]);
}

function colourText(cell) {
  if (!cell) return "no reading";
  const fg = typeof cell.fg === "string" && cell.fg ? cell.fg : "–";
  const bg = typeof cell.bg === "string" && cell.bg ? cell.bg : "–";
  return "fg " + fg + " · bg " + bg;
}

function firstTodayLines(fresh) {
  const first = obj(fresh && fresh.firstToday);
  if (!first) return [];
  const lines = [];
  for (const letter of LETTERS) {
    const hit = obj(first[letter]);
    if (!hit) continue;
    const at = snapshotTimeLabel(hit.at);
    lines.push("First " + letter + " today" + (at ? " " + at : "") + " @ " + money(hit.price));
  }
  const patterns = obj(first.patterns);
  if (patterns) {
    for (const name of Object.keys(PATTERN_NAME)) {
      const hit = obj(patterns[name]);
      if (!hit) continue;
      const at = snapshotTimeLabel(hit.at);
      lines.push(
        "First " + PATTERN_ICON[name] + " " + PATTERN_NAME[name] + " today" +
          (at ? " " + at : "") + " @ " + money(hit.price),
      );
    }
  }
  return lines;
}

function Section({ title, children }) {
  return (
    <section className="momx-filters-group momx-grade-why-section">
      <header className="momx-filters-grouphead">
        <span className="momx-grade-why-title">{title}</span>
      </header>
      {children}
    </section>
  );
}

export default function MomxGradeWhy({ row, record, onClose }) {
  // Esc closes this panel. Capture phase + stopPropagation so the same key
  // press does not also close a pop-out window behind it (the pop-out handler
  // additionally skips while .momx-grade-why-overlay is on screen).
  useEffect(() => {
    const onKey = (event) => {
      if (event.key !== "Escape") return;
      event.stopPropagation();
      if (onClose) onClose();
    };
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, [onClose]);

  const safeRow = obj(row) || {};
  const symbol = typeof safeRow.symbol === "string" ? safeRow.symbol : "";
  const grade = obj(safeRow.grade);
  const checks = obj(grade && grade.checks) || {};
  const letter = grade && typeof grade.letter === "string" ? grade.letter : null;
  const m5 = obj(safeRow.m5);
  // A row served on the BEAR board (spec 2026-09-24): its words flip.
  const isBearRow = safeRow.direction === "bear";
  const fresh = obj(safeRow.gradeFresh);
  const pattern = m5 && PATTERN_NAME[m5.pattern] ? m5.pattern : null;

  const title = letter
    ? "Scanner grade " + letter + " (experimental)"
    : pattern
      ? "Scanner pattern " + PATTERN_ICON[pattern] + " (no grade)"
      : "Scanner grade: none (experimental)";

  const firsts = firstTodayLines(fresh);

  // ---- Alignment
  const bullish = new Set(list(checks.skitBullish));
  const skitCount = finite(checks.skit);

  // ---- Push
  const pushRvol = list(checks.pushRvol);
  const news = obj(safeRow.news);
  const headline = news && typeof news.headline === "string" ? news.headline : "";
  const newsAge = finite(checks.newsAgeHours);

  // ---- Squeeze
  const fired = list(checks.sqzFired);
  const coiling = list(checks.sqzCoiling);

  // ---- Location
  const hlDegree = finite(checks.hlDegree);

  // ---- Trend strength (ADX). RECORDED, NOT GRADED: this section never
  // changes the letter above it, and a row with no reading simply says so.
  // `session` is stamped on a recorded EVENT, so it is present on a History /
  // event-derived row and absent on a live one.
  const adxRows = ADX_TFS
    .map((tf) => ({ tf, text: adxText(cellOf(safeRow, "adx", tf)) }))
    .filter((entry) => entry.text);
  // A History row carries NO adx at all: it is stripped from the stored
  // snapshot (momx/history.py GRADE_STRIPPED_FIELDS) because it doubled the
  // file the worker re-parses every build. That is a different statement from
  // "this row had no readable ADX", so it gets its own line - the durable
  // reading lives on the recorded grade / pattern event, not on the snapshot.
  const adxSaved = obj(safeRow.adx) !== null;
  const session = sessionLabel(safeRow.session);

  // ---- Timeline (oldest first, as it happened)
  // Each line carries BOTH times: the bar (or release bar) the change belongs
  // to, and when a scan saw it. They can be far apart - a 5m RVOL logged at
  // 13:53 can belong to the bucket that opened 13:40 - and only one of them
  // used to be on screen. timelineDetail() builds the whole line.
  const timeline = list(fresh && fresh.timeline)
    .filter((item) => obj(item) && typeof item.what === "string")
    .map((item) => ({
      detail: timelineDetail(safeRow, item) || item.what,
      ms: Date.parse(item.at),
      what: item.what,
    }))
    .sort((a, b) => (Number.isNaN(a.ms) ? 0 : a.ms) - (Number.isNaN(b.ms) ? 0 : b.ms));

  return createPortal(
    <div className="momx-filters-overlay momx-grade-why-overlay" onClick={onClose}>
      <div
        className="momx-filters momx-grade-why"
        role="dialog"
        aria-label={title}
        onClick={(event) => event.stopPropagation()}
      >
        <div className="momx-filters-head">
          <span>{symbol ? symbol + " · " : ""}{title}</span>
          <button type="button" onClick={onClose} aria-label="Close">x</button>
        </div>

        <div className="momx-filters-body">
          {firsts.length ? (
            <div className="momx-grade-why-firsts">
              {firsts.map((line) => (
                <p key={line}>{line}</p>
              ))}
            </div>
          ) : null}

          {!grade ? (
            <p className="momx-filters-note">
              This row has no grade yet - the scanner has not graded it (or it
              was recorded before grading existed).
            </p>
          ) : null}

          {grade ? (
            <Section title={"Alignment · SKIT " + (skitCount === null ? bullish.size : skitCount) + "/8 bullish"}>
              <ul className="momx-grade-why-list">
                {SKIT_TFS.map((tf) => {
                  const ok = bullish.has(tf);
                  return (
                    <li key={tf} className={ok ? "is-ok" : "is-no"}>
                      <span className="momx-grade-why-mark">{ok ? "✓" : "✗"}</span>
                      <span className="momx-grade-why-tf">{tfLabel(tf)}</span>
                      <span className="momx-grade-why-detail">{colourText(cellOf(safeRow, "skittles", tf))}</span>
                    </li>
                  );
                })}
              </ul>
            </Section>
          ) : null}

          {grade ? (
            <Section title={"Push · " + (checks.push ? "yes" : "no")}>
              <p className="momx-grade-why-line">
                <strong>RVOL:</strong>{" "}
                {pushRvol.length
                  ? pushRvol
                      .map((tf) => {
                        const cell = cellOf(safeRow, "rvol", tf);
                        const value = cell && cell.value !== undefined && cell.value !== null ? cell.value : "–";
                        return tf + " " + value;
                      })
                      .join(" · ")
                  : "no bullish RVOL on 5m-4h"}
              </p>
              <p className="momx-grade-why-line">
                <strong>News:</strong>{" "}
                {headline ? (
                  <>
                    {headline}
                    {newsAge !== null ? " (" + newsAge.toFixed(1) + " h ago)" : ""}
                    {checks.pushNews ? " - counts as push" : " - too old to count (over 12 h)"}
                  </>
                ) : (
                  "no headline"
                )}
              </p>
            </Section>
          ) : null}

          {grade ? (
            <Section title={"Squeeze · " + (checks.sqzOK ? "OK" : "coiling, not fired")}>
              <p className="momx-grade-why-line">
                <strong>Fired:</strong> {fired.length ? fired.map(tfLabel).join(", ") : "none"}
                {" · "}
                <strong>Coiling:</strong> {coiling.length ? coiling.map(tfLabel).join(", ") : "none"}
              </p>
              <p className="momx-grade-why-line momx-grade-why-dim">
                {SQZ_TFS.map((tf) => tfLabel(tf) + " " + colourText(cellOf(safeRow, "sqz", tf))).join(" | ")}
              </p>
            </Section>
          ) : null}

          {grade ? (
            <Section title="Location">
              <p className="momx-grade-why-line">
                H/L {hlDegreeText(hlDegree)} ·{" "}
                {/* On a bear row checks.aboveMid means BELOW the midpoint (momx/grade.py). */}
                {(checks.aboveMid ? !isBearRow : isBearRow) ? "above" : "below"} the 16 h midpoint
              </p>
            </Section>
          ) : null}

          <Section title="Momentum now (5m)">
            {m5 ? (
              <p className="momx-grade-why-line">
                {(isBearRow ? MOMENTUM_LABEL_BEAR : MOMENTUM_LABEL)[m5.state] || "–"}
                {m5.provisional ? " (provisional - candle still open)" : ""}
                {" · "}
                {chartLabel(m5.chart, isBearRow ? "bear" : "bull")}
                {" · trigger "}
                {money(m5.trigger)}
                {pattern ? " · " + PATTERN_ICON[pattern] + " " + PATTERN_NAME[pattern] : ""}
              </p>
            ) : (
              <p className="momx-grade-why-line momx-grade-why-dim">No 5-minute reading for this row.</p>
            )}
          </Section>

          <Section title="Trend strength (ADX)">
            {adxRows.length ? (
              <ul className="momx-grade-why-list">
                {adxRows.map((entry) => (
                  <li key={entry.tf}>
                    <span className="momx-grade-why-tf">{tfLabel(entry.tf)}</span>
                    <span className="momx-grade-why-detail">{entry.text}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="momx-grade-why-line momx-grade-why-dim">
                {adxSaved
                  ? "No ADX reading for this row."
                  : "No ADX reading saved for this History row."}
              </p>
            )}
            {session ? (
              <p className="momx-grade-why-line momx-grade-why-dim">
                Session window: {session}
              </p>
            ) : null}
            <p className="momx-grade-why-line momx-grade-why-dim">
              Recorded only - ADX is not part of the grade.
            </p>
          </Section>

          <Section title="Timeline (today, ET)">
            {timeline.length ? (
              <ul className="momx-grade-why-list">
                {timeline.map((item, index) => (
                  <li key={index + ":" + item.what}>
                    <span className="momx-grade-why-detail">{item.detail}</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="momx-grade-why-line momx-grade-why-dim">
                No changes observed yet today (blank after a scanner restart until the next change).
              </p>
            )}
          </Section>

          <p className="momx-grade-why-record">
            {letter ? trackRecordLine(record, letter) : "Track record is kept per grade letter."}
          </p>
          <p className="momx-grade-why-disclaimer">{GRADE_DISCLAIMER}</p>
        </div>
      </div>
    </div>,
    document.body,
  );
}
