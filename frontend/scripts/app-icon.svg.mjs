// The AGX app icon (2026-11-13, "fancy app icon like how Apple app have icons").
// One drawing, three framings: rounded "any" tile, full-bleed for iOS (which
// rounds it itself) and a maskable copy with the art inside the 80% safe zone.
// Run: node scripts/make-app-icons.mjs

export function appIconSvg({ shape = "rounded", scale = 1 } = {}) {
  const art = `
    <g transform="translate(256 256) scale(${scale}) translate(-256 -256)">
      <!-- soft glow behind the rising line -->
      <path d="M88 360 C 160 340, 196 300, 236 300 S 320 250, 352 206 S 410 150, 428 128"
            fill="none" stroke="#7df9ff" stroke-width="34" stroke-linecap="round" opacity="0.22" filter="url(#blur)"/>
      <!-- candles: glass bodies, cyan up / magenta down, like the charts -->
      <g filter="url(#drop)">
        <rect x="116" y="270" width="12" height="104" rx="6" fill="url(#wickM)"/>
        <rect x="98" y="292" width="48" height="62" rx="14" fill="url(#mag)"/>
        <rect x="196" y="214" width="12" height="118" rx="6" fill="url(#wickC)"/>
        <rect x="178" y="236" width="48" height="78" rx="14" fill="url(#cyan)"/>
        <rect x="276" y="186" width="12" height="102" rx="6" fill="url(#wickM)"/>
        <rect x="258" y="206" width="48" height="58" rx="14" fill="url(#mag)"/>
        <rect x="356" y="104" width="12" height="150" rx="6" fill="url(#wickC)"/>
        <rect x="338" y="126" width="48" height="104" rx="14" fill="url(#cyan)"/>
      </g>
      <!-- glass shine on each body -->
      <g fill="#ffffff" opacity="0.38">
        <rect x="104" y="297" width="14" height="40" rx="7"/>
        <rect x="184" y="241" width="14" height="54" rx="7"/>
        <rect x="264" y="211" width="14" height="38" rx="7"/>
        <rect x="344" y="131" width="14" height="76" rx="7"/>
      </g>
      <!-- the rising line with an arrow head -->
      <path d="M88 360 C 160 340, 196 300, 236 300 S 320 250, 352 206 S 400 156, 420 136"
            fill="none" stroke="url(#line)" stroke-width="13" stroke-linecap="round"/>
      <path d="M392 126 L 432 122 L 428 162 Z" fill="#ffffff" stroke="#ffffff" stroke-width="8" stroke-linejoin="round"/>
      <!-- base line -->
      <rect x="84" y="400" width="344" height="8" rx="4" fill="#ffffff" opacity="0.22"/>
    </g>`;
  const tile = shape === "rounded"
    ? `<rect x="0" y="0" width="512" height="512" rx="114" fill="url(#bg)"/>`
    : `<rect x="0" y="0" width="512" height="512" fill="url(#bg)"/>`;
  const clip = shape === "rounded" ? `clip-path="url(#tileClip)"` : "";
  return `<svg xmlns="http://www.w3.org/2000/svg" width="512" height="512" viewBox="0 0 512 512">
  <defs>
    <linearGradient id="bg" x1="0" y1="0" x2="0.9" y2="1">
      <stop offset="0" stop-color="#3b5bff"/>
      <stop offset="0.5" stop-color="#4b2fd6"/>
      <stop offset="1" stop-color="#1a0b4d"/>
    </linearGradient>
    <radialGradient id="glowTL" cx="0.22" cy="0.12" r="0.75">
      <stop offset="0" stop-color="#9fd8ff" stop-opacity="0.55"/>
      <stop offset="0.55" stop-color="#9fd8ff" stop-opacity="0"/>
    </radialGradient>
    <radialGradient id="glowBR" cx="0.95" cy="1" r="0.7">
      <stop offset="0" stop-color="#ff4fd8" stop-opacity="0.45"/>
      <stop offset="0.6" stop-color="#ff4fd8" stop-opacity="0"/>
    </radialGradient>
    <linearGradient id="shine" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#ffffff" stop-opacity="0.22"/>
      <stop offset="0.6" stop-color="#ffffff" stop-opacity="0"/>
    </linearGradient>
    <linearGradient id="cyan" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#b8fbff"/><stop offset="0.45" stop-color="#22d3ee"/><stop offset="1" stop-color="#0891b2"/>
    </linearGradient>
    <linearGradient id="mag" x1="0" y1="0" x2="0" y2="1">
      <stop offset="0" stop-color="#ffd1f4"/><stop offset="0.45" stop-color="#f472d0"/><stop offset="1" stop-color="#c026a3"/>
    </linearGradient>
    <linearGradient id="wickC" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#cffcff"/><stop offset="1" stop-color="#22d3ee"/></linearGradient>
    <linearGradient id="wickM" x1="0" y1="0" x2="0" y2="1"><stop offset="0" stop-color="#ffe0f7"/><stop offset="1" stop-color="#f472d0"/></linearGradient>
    <linearGradient id="line" x1="0" y1="1" x2="1" y2="0">
      <stop offset="0" stop-color="#ffffff" stop-opacity="0.55"/><stop offset="1" stop-color="#ffffff"/>
    </linearGradient>
    <filter id="blur" x="-20%" y="-20%" width="140%" height="140%"><feGaussianBlur stdDeviation="10"/></filter>
    <filter id="drop" x="-30%" y="-30%" width="160%" height="160%">
      <feDropShadow dx="0" dy="8" stdDeviation="8" flood-color="#0b0530" flood-opacity="0.55"/>
    </filter>
    <clipPath id="tileClip"><rect x="0" y="0" width="512" height="512" rx="114"/></clipPath>
  </defs>
  ${tile}
  <g ${clip}>
    <rect width="512" height="512" fill="url(#glowTL)"/>
    <rect width="512" height="512" fill="url(#glowBR)"/>
    ${art}
    <rect width="512" height="512" fill="url(#shine)"/>
  </g>
  ${shape === "rounded" ? `<rect x="1" y="1" width="510" height="510" rx="113" fill="none" stroke="#ffffff" stroke-opacity="0.18" stroke-width="2"/>` : ""}
</svg>`;
}
