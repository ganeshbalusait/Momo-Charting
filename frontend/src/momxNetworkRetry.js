// A phone on 5G through the public tunnel drops a scanner refresh now and
// then: the gateway log held 150 "connection aborted" lines on 2026-10-02,
// and Safari showed his MomX board the raw "FetchEvent.respondWith received
// an error: Load failed" as a red banner while nothing on the server had
// failed. A dropped connection is retried at once; only a connection that
// stays down reaches the screen, and then in plain words.

export const MOMX_NETWORK_RETRY_DELAYS_MS = [1500, 4000];

/** True for a request that never got an HTTP answer (the browser threw). */
export function isNetworkDrop(error) {
  if (!error || error.name === "AbortError") return false;
  if (error.name === "TypeError") return true;
  const text = String(error.message || "").toLowerCase();
  return (
    text.includes("load failed") ||
    text.includes("failed to fetch") ||
    text.includes("networkerror") ||
    text.includes("network connection was lost") ||
    text.includes("respondwith")
  );
}

/** fetch() that retries ONLY network drops; HTTP errors return as-is. */
export async function fetchWithNetworkRetry(url, init, options = {}) {
  const fetchImpl = options.fetchImpl || ((...args) => fetch(...args));
  const delays = options.delays || MOMX_NETWORK_RETRY_DELAYS_MS;
  const sleep = options.sleep || ((ms) => new Promise((resolve) => setTimeout(resolve, ms)));
  for (let attempt = 0; ; attempt += 1) {
    try {
      return await fetchImpl(url, init);
    } catch (error) {
      if (!isNetworkDrop(error) || attempt >= delays.length) throw error;
      await sleep(delays[attempt]);
    }
  }
}

/** The banner for a connection that stayed down through every retry. */
export function networkDropMessage(lastUpdatedAt) {
  const when = lastUpdatedAt ? new Date(lastUpdatedAt) : null;
  const stamp =
    when && !Number.isNaN(when.getTime())
      ? when.toLocaleTimeString("en-US", { hour: "numeric", minute: "2-digit", timeZone: "America/New_York" }) + " ET"
      : null;
  return (
    "Connection to the scanner dropped - retrying." +
    (stamp ? " The rows below are from " + stamp + "." : "")
  );
}
