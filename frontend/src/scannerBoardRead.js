import { fetchWithNetworkRetry } from "./momxNetworkRetry.js";

// Refresh cadence is 15s; a mobile download must be allowed to outlive it.
// The caller keeps one read in flight, so this does not multiply polls.
export const SCANNER_REQUEST_TIMEOUT_MS = 45000;
export const scannerReadRetryMessage = "Scanner refresh is taking longer — retrying. Any saved rows remain visible.";

function cancelled(signal) {
  return signal?.reason || new DOMException("Scanner read canceled", "AbortError");
}

function pause(ms, signal) {
  if (signal?.aborted) return Promise.reject(cancelled(signal));
  return new Promise((resolve, reject) => {
    const onAbort = () => { clearTimeout(timer); reject(cancelled(signal)); };
    const timer = setTimeout(() => { signal?.removeEventListener("abort", onAbort); resolve(); }, ms);
    signal?.addEventListener("abort", onAbort, { once: true });
  });
}

/** Complete headers AND JSON within a per-attempt deadline. An intentional
 * navigation/list cancellation never retries; one interrupted/slow read does.
 * No caller cache or UI state is replaced until a complete reply is returned. */
export async function readScannerBoard(url, { fetchImpl = globalThis.fetch, signal,
  timeoutMs = SCANNER_REQUEST_TIMEOUT_MS, retryDelayMs = 1500, onRetry } = {}) {
  for (let attempt = 0; attempt < 2; attempt += 1) {
    if (signal?.aborted) throw cancelled(signal);
    const controller = new AbortController();
    const onAbort = () => controller.abort(signal.reason);
    signal?.addEventListener("abort", onAbort, { once: true });
    let expired = false;
    const timer = setTimeout(() => { expired = true; controller.abort(); }, timeoutMs);
    try {
      return await fetchWithNetworkRetry(url, { cache: "no-store", priority: "high", signal: controller.signal }, {
        fetchImpl: async (input, init) => {
          const requestedAt = Date.now();
          const response = await fetchImpl(input, init);
          const payload = response.ok ? await response.json() : null;
          return { response, payload, requestedAt };
        },
      });
    } catch (error) {
      if (signal?.aborted) throw cancelled(signal);
      const interrupted = expired || error?.name === "AbortError";
      if (!interrupted) throw error;
      if (attempt === 1) {
        const failure = new Error("Scanner refresh is still slow or interrupted. It will retry automatically; any saved rows remain visible.");
        failure.name = "ScannerReadTimeout";
        throw failure;
      }
    } finally {
      clearTimeout(timer);
      signal?.removeEventListener("abort", onAbort);
    }
    onRetry?.(scannerReadRetryMessage);
    await pause(retryDelayMs, signal);
  }
}
