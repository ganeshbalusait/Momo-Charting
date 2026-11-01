// Release chart HTTP connections only after the last visible pane leaves.
export function createChartRequestOwners() {
  const owners = new Map(), requests = new Map();
  const key = value => String(value || "").trim().toUpperCase();
  const track = (value, controller, requiresOwner) => {
    const symbol = key(value);
    if (!symbol || (requiresOwner && !owners.has(symbol))) {
      controller.abort();
      return () => {};
    }
    if (!requests.has(symbol)) requests.set(symbol, new Set());
    const pending = requests.get(symbol);
    pending.add(controller);
    return () => {
      pending.delete(controller);
      if (!pending.size && requests.get(symbol) === pending) requests.delete(symbol);
    };
  };
  return {
    retain(value) {
      const symbol = key(value);
      if (!symbol) return () => {};
      owners.set(symbol, (owners.get(symbol) || 0) + 1);
      let released = false;
      return () => {
        if (released) return;
        released = true;
        const remaining = (owners.get(symbol) || 1) - 1;
        if (remaining > 0) { owners.set(symbol, remaining); return; }
        owners.delete(symbol);
        for (const controller of requests.get(symbol) || []) controller.abort();
        requests.delete(symbol);
      };
    },
    trackArchive(value, controller) {
      return track(value, controller, true);
    },
    // Speculative bounded warm-ups may start before a pane owns the ticker.
    trackRequest(value, controller) { return track(value, controller, false); },
  };
}
