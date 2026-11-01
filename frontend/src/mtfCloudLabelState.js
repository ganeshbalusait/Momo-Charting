// State of one MTF cloud label chip (MomoX3 style, split top/bottom).
//   top    = EMA 9 vs EMA 20 trend (shared_MTF_CloudLabel25: a tie is Bear)
//   bottom = last close above / below EMA 9
//   strong = that half's gap widened on the newest bar (hover text only; the
//            chip is always full-strength cyan/magenta, his call 2026-10-16)
//   freshCross = EMA 9 crossed EMA 20 on the newest bar ("bull" | "bear" | null)

function emaSeries(values, period) {
  if (!values.length) return [];
  const multiplier = 2 / (period + 1);
  let ema = values[0];
  return values.map((value) => (ema = value * multiplier + ema * (1 - multiplier)));
}

export function mtfCloudLabelState(closes) {
  const values = (Array.isArray(closes) ? closes : [])
    .map(Number)
    .filter(Number.isFinite);
  if (!values.length) return null;
  const ema9 = emaSeries(values, 9);
  const ema20 = emaSeries(values, 20);
  const last = values.length - 1;
  const spread = ema9[last] - ema20[last];
  const gap = values[last] - ema9[last];
  const tone = spread > 0 ? "bull" : "bear";
  const priceTone = gap > 0 ? "bull" : "bear";
  if (last < 1) {
    return { ema9: ema9[last], ema20: ema20[last], tone, priceTone, trendStrong: false, priceStrong: false, freshCross: null };
  }
  const previousSpread = ema9[last - 1] - ema20[last - 1];
  const previousGap = values[last - 1] - ema9[last - 1];
  const previousTone = previousSpread > 0 ? "bull" : "bear";
  return {
    ema9: ema9[last],
    ema20: ema20[last],
    tone,
    priceTone,
    trendStrong: tone === "bull" ? spread > previousSpread : spread < previousSpread,
    priceStrong: priceTone === "bull" ? gap > previousGap : gap < previousGap,
    freshCross: previousTone !== tone ? tone : null,
  };
}
