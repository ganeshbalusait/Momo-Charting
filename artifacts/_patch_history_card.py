"""The lightning bolt opens a card in History too - showing THAT MOMENT.

"can we do it for History also?" (2026-09-02), on the Watchlist History view
filtered to IREN.

A history row is a full archived snapshot - rvol, sqz, skittles, highLow,
news, scanReasons, pctChange, last, industry - so its card is the card he
already has, rendered from the snapshot instead of from the live board. It is
stamped with the snapshot's own time so it can never be mistaken for now.

TWO THINGS THE SNAPSHOT CANNOT HONESTLY SHOW, and both are suppressed rather
than filled with today's numbers:

* 1-hr high/low. Not recorded then - the field did not exist - and it is a
  live measure. Printing today's under a 07:26 row would be a fabrication.
* The option walls, on a row from a PREVIOUS DAY. Open interest changes
  overnight, so today's walls were genuinely in force for a row stamped
  today, and genuinely were not for one stamped last week. Same-day rows keep
  the walls; older rows say why they have none.

The news freshness gate is already judged as of the snapshot (see
MomxHistoryRow), and the card inherits that by being handed the same instant.
"""
import io

p = "frontend/src/MomxScannerPanel.jsx"
s = io.open(p, encoding="utf-8", newline="").read()

# --- the history row's bolt becomes a button ------------------------------
OLD = '''                <span className="momx-badge is-fires" title={"Signals fired: " + fires.join(" · ")}>
                  <Zap size={10} aria-hidden="true" />
                </span>'''
NEW = '''                <button
                  type="button"
                  className="momx-badge is-fires"
                  title={"Signals fired: " + fires.join(" · ") + " - click for this moment's card"}
                  aria-label={"Open the " + (row.symbol || entry.symbol) + " card for this snapshot"}
                  onClick={(event) => {
                    event.stopPropagation();
                    if (onOpenSnapshot) onOpenSnapshot(entry);
                  }}
                >
                  <Zap size={10} aria-hidden="true" />
                </button>'''
assert s.count(OLD) == 1, "history badge anchor"
s = s.replace(OLD, NEW)

OLD = "const MomxHistoryRow = memo(function MomxHistoryRow({ entry, newsTime }) {"
NEW = "const MomxHistoryRow = memo(function MomxHistoryRow({ entry, newsTime, onOpenSnapshot }) {"
assert s.count(OLD) == 1, "history row signature anchor"
s = s.replace(OLD, NEW)

s = s.replace(
    '<MomxHistoryRow key={entry.key} entry={entry} newsTime={newsOnly} />',
    '<MomxHistoryRow key={entry.key} entry={entry} newsTime={newsOnly} onOpenSnapshot={onOpenSnapshot} />',
)

OLD = '''function MomxHistorySection({ list, payload, loading, error, date, query, search, onDate, onSearch, onRefresh, onOffset'''
NEW = '''function MomxHistorySection({ onOpenSnapshot, list, payload, loading, error, date, query, search, onDate, onSearch, onRefresh, onOffset'''
assert s.count(OLD) == 1, "history section signature anchor"
s = s.replace(OLD, NEW)

OLD = '''        <MomxHistorySection'''
NEW = '''        <MomxHistorySection
          onOpenSnapshot={onOpenSnapshot}'''
assert s.count(OLD) == 1, "history section render anchor"
s = s.replace(OLD, NEW)

# --- opening a snapshot card ---------------------------------------------
OLD = '''  // Drag from the title bar, resize from the corner. Pointer capture rather'''
NEW = '''  // A card for one archived MOMENT, opened from the History view. Same window
  // kind as a live card; what differs is that its data is frozen and stamped,
  // so it can never be read as "now".
  const onOpenSnapshot = useCallback((entry) => {
    const snapshot = entry && typeof entry.row === "object" ? entry.row : null;
    const wanted = String((snapshot && snapshot.symbol) || (entry && entry.symbol) || "")
      .trim()
      .toUpperCase();
    if (!snapshot || !wanted) return;
    const at = entry.at || entry.lastSeenAt || "";
    const id = "snap-" + wanted + "-" + at;
    setPopouts((windows) => {
      const existing = windows.find((w) => w.id === id);
      if (existing) {
        popoutTopRef.current += 1;
        const z = popoutTopRef.current;
        return windows.map((w) => (w.id === existing.id ? { ...w, z } : w));
      }
      popoutTopRef.current += 1;
      const cards = windows.filter((w) => w.kind === "card").length;
      const base = defaultPopoutRect(cards);
      return [
        ...windows,
        {
          id,
          kind: "card",
          symbol: wanted,
          list: activeList || "",
          snapshot,
          snapshotAt: at,
          // Open interest changes overnight, so today's walls WERE the walls
          // in force for a row stamped today, and were not for an older one.
          sameDay: String(at).slice(0, 10) === new Date().toISOString().slice(0, 10),
          rect: clampPopoutRect({ x: base.x, y: base.y, w: 820, h: 470 }),
          z: popoutTopRef.current,
        },
      ];
    });
  }, [activeList]);

  // Drag from the title bar, resize from the corner. Pointer capture rather'''
assert s.count(OLD) == 1, "onOpenSnapshot anchor"
s = s.replace(OLD, NEW)

# --- render a frozen card when the window carries a snapshot --------------
OLD = '''                <MomxTickerCard
                  row={rows.find((r) => String(r.symbol || "").toUpperCase() === win.symbol) || null}
                  stampLabel={formatUpdatedAt(boardView.generatedAt)}
                  nowMs={momentumNow}
                />'''
NEW = '''                <MomxTickerCard
                  row={
                    win.snapshot
                      || rows.find((r) => String(r.symbol || "").toUpperCase() === win.symbol)
                      || null
                  }
                  stampLabel={
                    win.snapshot
                      ? formatUpdatedAt(win.snapshotAt) + " · from History"
                      : formatUpdatedAt(boardView.generatedAt)
                  }
                  // A snapshot's news freshness is judged as of the snapshot,
                  // the way the history ROW already judges it: by tonight
                  // every headline would fail a Date.now() gate and quietly
                  // disappear from a card about this morning.
                  nowMs={win.snapshot ? Date.parse(win.snapshotAt) || momentumNow : momentumNow}
                  frozen={Boolean(win.snapshot)}
                  allowWalls={!win.snapshot || win.sameDay}
                />'''
assert s.count(OLD) == 1, "card render anchor"
s = s.replace(OLD, NEW)

# card windows are titled by symbol; a snapshot says which moment
OLD = '''              <b>{win.kind === "card" ? win.symbol : win.list || "MomX Scanner"}</b>'''
NEW = '''              <b>
                {win.kind === "card"
                  ? win.symbol + (win.snapshot ? " · snapshot" : "")
                  : win.list || "MomX Scanner"}
              </b>'''
assert s.count(OLD) == 1, "title anchor"
s = s.replace(OLD, NEW)
io.open(p, "w", encoding="utf-8", newline="").write(s)
print("MomxScannerPanel.jsx: History bolts open a frozen card")

# ---------------------------------------------------------------------------
p = "frontend/src/MomxTickerCard.jsx"
s = io.open(p, encoding="utf-8", newline="").read()

OLD = '''export default function MomxTickerCard({ row, stampLabel, nowMs = Date.now() }) {'''
NEW = '''export default function MomxTickerCard({
  row,
  stampLabel,
  nowMs = Date.now(),
  // `frozen` = this is an archived moment from History, not the live board.
  // It suppresses the two things a snapshot cannot honestly carry rather than
  // filling them with today's numbers under yesterday's timestamp.
  frozen = false,
  allowWalls = true,
}) {'''
assert s.count(OLD) == 1, "card signature anchor"
s = s.replace(OLD, NEW)

OLD = '''        <HighOiWalls symbol={row.symbol} fallbackLast={row.last} />'''
NEW = '''        {allowWalls ? (
          <HighOiWalls symbol={row.symbol} fallbackLast={row.last} />
        ) : (
          <div className="momx-walls">
            <h4>High-OI walls</h4>
            <p className="momx-walls-note">
              not kept for past days — open interest changes overnight, so today&apos;s walls were
              not these
            </p>
          </div>
        )}'''
assert s.count(OLD) == 1, "walls gate anchor"
s = s.replace(OLD, NEW)

OLD = '''        <dl className="momx-card-hourly">
          <div>
            <dt>1-hr high</dt>
            <dd>{plain(hourly.high)}</dd>
          </div>
          <div>
            <dt>1-hr low</dt>
            <dd>{plain(hourly.low)}</dd>
          </div>
        </dl>'''
NEW = '''        {frozen ? null : (
          // Never on a snapshot: the hour's range was not recorded then, and
          // printing the CURRENT hour's under an 07:26 row would be invention.
          <dl className="momx-card-hourly">
            <div>
              <dt>1-hr high</dt>
              <dd>{plain(hourly.high)}</dd>
            </div>
            <div>
              <dt>1-hr low</dt>
              <dd>{plain(hourly.low)}</dd>
            </div>
          </dl>
        )}'''
assert s.count(OLD) == 1, "hourly anchor"
s = s.replace(OLD, NEW)
io.open(p, "w", encoding="utf-8", newline="").write(s)
print("MomxTickerCard.jsx: frozen snapshots suppress live-only sections")
