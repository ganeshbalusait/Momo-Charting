"""Background pop-out windows refresh every 60s; the one in front stays at 15s.

His choice, made in the session working alongside this one after being shown
three options (throttle / leave it / measure at the open first) with the
measured numbers and the mitigations that cap the risk.

Worth recording that I had WITHDRAWN this recommendation before he chose it.
The scary arithmetic I first relayed - four windows x a 1MB board - cannot
happen: opening a list that already has a window RAISES it rather than adding
a second (see onPopOut), only Watchlist carries 357 names, and pop-outs open
with matches-only ON. The peer then measured one big board plus a second
window at 1.8% main thread with zero stalls over 200ms. He took the throttle
anyway, and it is defensible on its own terms: a background window is by
definition one he is not reading, so being wrong in that direction costs
almost nothing.

Scope, deliberately narrow:

* only EMBEDDED boards - the boards living inside pop-out windows - are ever
  slowed. The page's own board keeps 15s, because that is the app.
* the FRONT window (highest z, the one he raised last) keeps 15s too. It is
  the one he is reading.
* card windows are untouched: they render from the parent's rows and hold
  their chain for ten minutes, so they never polled the board at all.

A backgrounded window still refreshes - a minute, not never - and is put back
on 15s the moment he clicks it, because raising a window changes its z and
this reads z.
"""
import io

p = "frontend/src/MomxScannerPanel.jsx"
s = io.open(p, encoding="utf-8", newline="").read()

OLD = "const MOMX_POLL_MS = 15000;"
NEW = '''const MOMX_POLL_MS = 15000;

//: A pop-out board he is NOT looking at - anything behind the front window -
//: refreshes on this instead (his call, 2026-09-02). Still refreshing, just
//: not four boards a quarter-minute. The front window and the page itself
//: stay on MOMX_POLL_MS, and raising a window restores it immediately.
const MOMX_BACKGROUND_POLL_MS = 60000;'''
assert s.count(OLD) == 1, "poll constant anchor"
s = s.replace(OLD, NEW)

OLD = '''export default function MomxScannerPanel({ initialList = null, embedded = false }) {'''
NEW = '''export default function MomxScannerPanel({ initialList = null, embedded = false, background = false }) {'''
assert s.count(OLD) == 1, "signature anchor"
s = s.replace(OLD, NEW)

OLD = '''    const timer = window.setInterval(tick, MOMX_POLL_MS);

    const onVisibilityChange = () => {'''
NEW = '''    // A backgrounded pop-out board refreshes every minute instead of every
    // 15s. `background` is recomputed by the parent whenever the stacking
    // order changes, and this effect lists it as a dependency, so raising a
    // window swaps the interval back to 15s on the spot.
    const timer = window.setInterval(tick, background ? MOMX_BACKGROUND_POLL_MS : MOMX_POLL_MS);

    const onVisibilityChange = () => {'''
assert s.count(OLD) == 1, "interval anchor"
s = s.replace(OLD, NEW)

# the effect must re-run when the window is raised or covered
marker = "    const timer = window.setInterval(tick, background ? MOMX_BACKGROUND_POLL_MS : MOMX_POLL_MS);"
tail = s.index(marker)
close = s.index("\n  }, [", tail)
end = s.index("]);", close)
deps = s[close:end]
assert "background" not in deps, "background already a dependency?"
s = s[:end] + ", background" + s[end:]

# --- the parent decides which window is in front --------------------------
OLD = '''              <MomxScannerPanel initialList={win.list} embedded />'''
NEW = '''              <MomxScannerPanel
                initialList={win.list}
                embedded
                // The front window is the one he raised last. Everything
                // behind it is a board he is not reading.
                background={win.z !== frontZ}
              />'''
assert s.count(OLD) == 1, "embedded render anchor"
s = s.replace(OLD, NEW)

OLD = '''  const rect = popoutRect || defaultPopoutRect();'''
NEW = '''  const rect = popoutRect || defaultPopoutRect();'''
# (no-op guard: the rect line was removed in the multi-window refactor)

OLD = '''  return (
    <>
      {panel}
      {popouts.map((win) =>'''
NEW = '''  // Highest z = the window he raised last. Read once per render rather than
  // per window, so N windows do not each scan the list.
  const frontZ = popouts.reduce((top, win) => (win.z > top ? win.z : top), -Infinity);

  return (
    <>
      {panel}
      {popouts.map((win) =>'''
assert s.count(OLD) == 1, "render anchor"
s = s.replace(OLD, NEW)
io.open(p, "w", encoding="utf-8", newline="").write(s)
print("MomxScannerPanel.jsx: background pop-out boards poll at 60s")
