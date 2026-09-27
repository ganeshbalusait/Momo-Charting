// Colour rule for the scanner News column. The tone follows the headline's
// sentiment only - never its age - so a green cell always means good news and
// a red cell always means bad news, however old the headline is.

const POSITIVE = new Set(["strong", "positive", "bullish", "good"]);
const NEGATIVE = new Set(["negative", "bearish", "bad"]);

export function newsSentimentTone(sentiment) {
  const text = String(sentiment ?? "").trim();
  const key = text.toLowerCase();
  if (POSITIVE.has(key)) return { key: "positive", label: text, flash: true };
  if (NEGATIVE.has(key)) return { key: "negative", label: text, flash: true };
  return { key: "neutral", label: text || "Neutral", flash: false };
}
