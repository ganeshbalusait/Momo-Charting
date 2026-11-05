// One lifetime signal covers headers AND body reads. A fetch promise settles
// at headers, so dropping its controller there would leave a large .json()
// download running after the scanner has unmounted.
export function createScannerRequestScope(fetchImpl = (...args) => globalThis.fetch(...args)) {
  let lifetime = new AbortController();
  let closed = false;
  const combinedSignal = (caller) => {
    if (!caller) return lifetime.signal;
    if (typeof AbortSignal.any === "function") return AbortSignal.any([lifetime.signal, caller]);
    const combined = new AbortController();
    for (const signal of [lifetime.signal, caller]) {
      if (signal.aborted) { combined.abort(signal.reason); break; }
      signal.addEventListener("abort", () => combined.abort(signal.reason), { once: true });
    }
    return combined.signal;
  };
  return {
    fetch(input, options = {}) {
      const method = String(options.method || input?.method || "GET").toUpperCase();
      // User-initiated writes keep their existing completion semantics.
      if (method !== "GET" && method !== "HEAD") return fetchImpl(input, options);
      if (closed) return Promise.reject(new DOMException("Scanner read scope closed", "AbortError"));
      return fetchImpl(input, { ...options, signal: combinedSignal(options.signal || input?.signal) });
    },
    abortReads() { closed = true; lifetime.abort(); },
    resume() {
      if (closed || lifetime.signal.aborted) lifetime = new AbortController();
      closed = false;
    },
  };
}

// True for a read that was cancelled (the scanner unmounting, a remount, or a
// caller's deadline) rather than one that failed. A loader whose in-flight
// guard dropped a call while the cancelled read was settling retries on this.
export function isAbortError(error) {
  return Boolean(error) && error.name === "AbortError";
}
