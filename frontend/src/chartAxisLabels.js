function axisLabelParts(label) {
  if (Array.isArray(label?.parts) && label.parts.length) return label.parts;
  return [{ key: label?.key || "", title: label?.title || "", color: label?.color || "#8fa7c5" }];
}

function isPivotPart(part) {
  return String(part?.key || "").startsWith("pivot-");
}

function isOiPart(part) {
  return String(part?.key || "").startsWith("oi-wall-");
}

function axisLabelPriority(label) {
  const keys = axisLabelParts(label).map(({ key }) => String(key || ""));
  if (keys.some((key) => key === "live-price")) return 1_000;
  if (keys.some((key) => key.startsWith("price-alert-"))) return 900;
  if (keys.some((key) => key.startsWith("oi-wall-"))) {
    return 800 + Math.max(0, Number(label?.priority) || 0);
  }
  if (keys.some((key) => key.startsWith("session-level-"))) return 700;
  if (keys.some((key) => key.startsWith("pivot-"))) return 600;
  if (keys.some((key) => key.startsWith("persons-pivot-"))) return 550;
  return 400 + Math.max(0, Number(label?.priority) || 0);
}

function nearestAvailableTop(desiredTop, occupiedTops, minimumTop, maximumTop, minimumGap) {
  const candidates = [
    Math.max(minimumTop, Math.min(maximumTop, desiredTop)),
    minimumTop,
    maximumTop,
    ...occupiedTops.flatMap((top) => [top - minimumGap, top + minimumGap]),
  ]
    .filter((top) => top >= minimumTop && top <= maximumTop)
    .sort((left, right) => (
      Math.abs(left - desiredTop) - Math.abs(right - desiredTop)
      || left - right
    ));
  return candidates.find((top) => (
    occupiedTops.every((occupied) => Math.abs(occupied - top) >= minimumGap)
  ));
}

export function layoutChartAxisLabels(labels, {
  paneHeight,
  padding = 9,
  minimumGap = 18,
} = {}) {
  const height = Number(paneHeight);
  const gap = Math.max(1, Number(minimumGap) || 18);
  const inset = Math.max(0, Number(padding) || 0);
  const minimumTop = inset;
  const maximumTop = Math.max(minimumTop, height - inset);
  if (!Number.isFinite(height) || height <= inset * 2) return [];

  const candidates = (Array.isArray(labels) ? labels : [])
    .flatMap((label, index) => {
      const top = Number(label?.top);
      if (!Number.isFinite(top)) return [];
      return [{
        ...label,
        top: Math.max(minimumTop, Math.min(maximumTop, top)),
        _axisOrder: index,
        _axisPriority: axisLabelPriority(label),
      }];
    });

  // LIVE, alerts, and OI walls identify an exact tradable price. Keep their
  // surviving chips on that exact y-coordinate, choosing the most important
  // one when two fixed labels cannot both fit.
  const fixed = [];
  candidates
    .filter((label) => label.preservePricePosition)
    .sort((left, right) => (
      right._axisPriority - left._axisPriority
      || left._axisOrder - right._axisOrder
    ))
    .forEach((label) => {
      if (fixed.some((existing) => Math.abs(existing.top - label.top) < gap)) return;
      fixed.push(label);
    });

  // Ordinary study labels may slide slightly along the axis. Place the most
  // useful labels first and hide only the labels for which no collision-free
  // slot remains. Their study lines stay visible on the chart.
  const positioned = [...fixed];
  const occupiedTops = fixed.map(({ top }) => top);
  candidates
    .filter((label) => !label.preservePricePosition)
    .sort((left, right) => (
      right._axisPriority - left._axisPriority
      || left._axisOrder - right._axisOrder
    ))
    .forEach((label) => {
      const top = nearestAvailableTop(label.top, occupiedTops, minimumTop, maximumTop, gap);
      if (top == null) return;
      occupiedTops.push(top);
      positioned.push({ ...label, top });
    });

  return positioned
    .sort((left, right) => left.top - right.top || left._axisOrder - right._axisOrder)
    .map(({ _axisOrder, _axisPriority, ...label }) => label);
}

export function mergePivotAndOiAxisLabels(labels, tolerance = 0.000001) {
  return (Array.isArray(labels) ? labels : []).reduce((merged, label) => {
    const labelParts = axisLabelParts(label);
    const labelHasPivot = labelParts.some(isPivotPart);
    const labelHasOi = labelParts.some(isOiPart);
    const matchingIndex = merged.findIndex((existing) => {
      if (Math.abs(Number(existing.price) - Number(label.price)) > tolerance) return false;
      const existingParts = axisLabelParts(existing);
      return (labelHasPivot && existingParts.some(isOiPart))
        || (labelHasOi && existingParts.some(isPivotPart));
    });
    if (matchingIndex < 0) return [...merged, { ...label, parts: labelParts }];

    const existing = merged[matchingIndex];
    const parts = [...axisLabelParts(existing), ...labelParts]
      .filter((part, index, all) => all.findIndex(({ key }) => key === part.key) === index);
    const oiLabel = labelHasOi ? label : existing;
    const combined = {
      ...existing,
      ...oiLabel,
      key: parts.map(({ key }) => key).join("+"),
      title: parts.map(({ title }) => title).filter(Boolean).join(" · "),
      parts,
      preservePricePosition: true,
      priority: Math.max(Number(existing.priority || 0), Number(label.priority || 0)),
    };
    return merged.map((item, index) => (index === matchingIndex ? combined : item));
  }, []);
}
