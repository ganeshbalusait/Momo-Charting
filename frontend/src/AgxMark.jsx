// AGX brand mark — three candlesticks in the product's cyan/magenta pair.
//
// Renders the candles only, no surrounding tile: `.brand-mark` already supplies
// the 30px cyan-bordered box, so drawing another badge here would double it up.
// Colours are literals rather than currentColor because the magenta candle is
// part of the mark's identity, not a themeable accent.

const CYAN = "#22d3ee";
const MAGENTA = "#ff3d9a";

export default function AgxMark({ size = 18, title }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="30 33 50 64"
      role={title ? "img" : undefined}
      aria-hidden={title ? undefined : true}
      aria-label={title}
      focusable="false"
    >
      {title ? <title>{title}</title> : null}
      {/* left — cyan */}
      <rect x="37.5" y="40" width="4" height="52" rx="1" fill={CYAN} opacity="0.55" />
      <rect x="34" y="52" width="11" height="26" rx="2" fill={CYAN} />
      {/* centre — magenta, the tallest */}
      <rect x="53.5" y="36" width="4" height="58" rx="1" fill={MAGENTA} opacity="0.55" />
      <rect x="50" y="46" width="11" height="34" rx="2" fill={MAGENTA} />
      {/* right — cyan */}
      <rect x="69.5" y="44" width="4" height="48" rx="1" fill={CYAN} opacity="0.55" />
      <rect x="66" y="56" width="11" height="22" rx="2" fill={CYAN} />
    </svg>
  );
}
