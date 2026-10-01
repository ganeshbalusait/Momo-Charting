"""Two defects in the walls block, both visible in his BOIL screenshot.

1. "no option-chain walls for this one" ON A SYMBOL THAT HAS THEM. Measured
   straight after: /api/oi-finder?symbol=BOIL returns 10 calls and 14 puts,
   spot 20.965, EM 0.7308, and a 21.0 strike holding 6,363 open interest -
   squarely inside the +-EM2 band.

   The cache was the bug. It stored the PROMISE keyed by symbol for ten
   minutes whatever came back, so the first request for a cold symbol - which
   the server answers while it is still warming, with no rows - was pinned as
   the answer for the next ten minutes. A cache that remembers failures for
   as long as it remembers successes turns a transient miss into a permanent
   one, which is this repo's "truthy-but-meaningless" shape wearing a
   different hat.

   Now only a payload with rows is remembered. An empty or failed one is
   dropped and retried, with a short backoff, up to three times - which is
   what a warming chain needs, since it fills in within a second or two.

2. THE LAYOUT COLLAPSED when the walls were missing: a huge empty band with
   the fires line floating in the middle of it. `.momx-card-fires` carries
   `flex: 1 1 260px`, which was written when its parent was a ROW; it is now
   in a COLUMN, where flex-grow means grow VERTICALLY. It ate all the spare
   height and pushed everything apart. It is content-sized now, and the foot
   no longer stretches its children.

   The note also rendered as a bare <p> where the walls block would be, so
   the hourly numbers slid up next to it. It lives inside the walls container
   now, so the two columns hold their shape whether or not there are walls.
"""
import io

p = "frontend/src/MomxTickerCard.jsx"
s = io.open(p, encoding="utf-8", newline="").read()

# --- only remember an answer that actually answered ------------------------
OLD = '''const WALLS_TTL_MS = 10 * 60 * 1000;
const wallsCache = new Map();   // SYMBOL -> { at, promise }

function loadChain(symbol) {
  const key = String(symbol || "").toUpperCase();
  const hit = wallsCache.get(key);
  if (hit && Date.now() - hit.at < WALLS_TTL_MS) return hit.promise;
  const promise = fetch("/api/oi-finder?symbol=" + encodeURIComponent(key), { cache: "no-store" })
    .then((response) => (response.ok ? response.json() : null))
    .catch(() => null);
  wallsCache.set(key, { at: Date.now(), promise });
  return promise;
}'''
NEW = '''const WALLS_TTL_MS = 10 * 60 * 1000;
const WALLS_RETRIES = 3;
const WALLS_RETRY_MS = 1500;
const wallsCache = new Map();   // SYMBOL -> { at, promise }  (successes only)

function chainHasRows(payload) {
  if (!payload || typeof payload !== "object") return false;
  return (payload.callRows || []).length > 0 || (payload.putRows || []).length > 0;
}

async function fetchChainOnce(symbol) {
  try {
    const response = await fetch("/api/oi-finder?symbol=" + encodeURIComponent(symbol), {
      cache: "no-store",
    });
    return response.ok ? await response.json() : null;
  } catch {
    return null;
  }
}

/** The chain for one symbol. ONLY a payload with rows is cached.
 *
 *  The first request for a cold symbol is answered while the server is still
 *  warming, with no rows. Caching that for ten minutes - which the first cut
 *  of this did - turned a two-second wait into "no option-chain walls for
 *  this one" for ten minutes, on BOIL, which has plenty (2026-09-02).
 */
function loadChain(symbol) {
  const key = String(symbol || "").toUpperCase();
  const hit = wallsCache.get(key);
  if (hit && Date.now() - hit.at < WALLS_TTL_MS) return hit.promise;

  const promise = (async () => {
    for (let attempt = 0; attempt < WALLS_RETRIES; attempt += 1) {
      const payload = await fetchChainOnce(key);
      if (chainHasRows(payload)) return payload;
      if (attempt < WALLS_RETRIES - 1) {
        await new Promise((resolve) => setTimeout(resolve, WALLS_RETRY_MS));
      }
    }
    return null;
  })();

  // Held provisionally so two cards opened together share one request, then
  // DROPPED unless it found rows - a miss must not outlive its own attempt.
  wallsCache.set(key, { at: Date.now(), promise });
  promise.then((payload) => {
    if (!chainHasRows(payload) && wallsCache.get(key)?.promise === promise) {
      wallsCache.delete(key);
    }
  });
  return promise;
}'''
assert s.count(OLD) == 1, "cache anchor"
s = s.replace(OLD, NEW)

# --- the note keeps the walls column's shape ------------------------------
OLD = '''  if (state === "loading") return <p className="momx-walls-note">reading the option chain…</p>;
  if (!model || (model.calls.length === 0 && model.puts.length === 0)) {
    return <p className="momx-walls-note">no option-chain walls for this one</p>;
  }'''
NEW = '''  // The note sits INSIDE the walls container so the card keeps its two
  // columns whether or not there are walls; as a bare <p> it collapsed and
  // the hourly numbers slid up beside it (his BOIL screenshot).
  if (state === "loading" || state === "idle") {
    return (
      <div className="momx-walls">
        <h4>High-OI walls</h4>
        <p className="momx-walls-note">reading the option chain…</p>
      </div>
    );
  }
  if (!model || (model.calls.length === 0 && model.puts.length === 0)) {
    return (
      <div className="momx-walls">
        <h4>High-OI walls</h4>
        <p className="momx-walls-note">no option-chain walls for this one</p>
      </div>
    );
  }'''
assert s.count(OLD) == 1, "note anchor"
s = s.replace(OLD, NEW)
io.open(p, "w", encoding="utf-8", newline="").write(s)
print("MomxTickerCard.jsx: warming answers are no longer cached; note keeps its column")

# ---------------------------------------------------------------------------
p = "frontend/src/index.css"
css = io.open(p, encoding="utf-8", newline="").read()

OLD = '''.momx-card-fires {
  display: flex;
  align-items: center;
  gap: 7px;
  flex: 1 1 260px;
  margin: 0;'''
NEW = '''.momx-card-fires {
  display: flex;
  align-items: center;
  gap: 7px;
  /* Content-sized. `flex: 1 1 260px` here was written when the parent was a
     ROW; in the column it sits in now, grow means grow VERTICALLY, and it ate
     the card's spare height and left the fires floating in a void (his BOIL
     screenshot, 2026-09-02). */
  flex: 0 0 auto;
  margin: 0;'''
assert css.count(OLD) == 1, "fires flex anchor"
css = css.replace(OLD, NEW)

OLD = '''.momx-card-foot { align-items: stretch; }'''
NEW = '''/* Both columns hug the top. Stretching them made the shorter one grow to the
   height of the taller, which is what let the fires line drift to the middle
   of the card. */
.momx-card-foot { align-items: flex-start; }'''
assert css.count(OLD) == 1, "foot align anchor"
css = css.replace(OLD, NEW)

css += '''
/* The walls column keeps its width whether it has walls or only a note, so
   the hourly/fires column beside it does not move when the chain is slow. */
.momx-walls-note { min-height: 22px; }
'''
io.open(p, "w", encoding="utf-8", newline="").write(css)
print("index.css: fires is content-sized, the foot no longer stretches")
