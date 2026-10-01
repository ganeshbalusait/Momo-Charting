// Directions for someone who has just been added, not a credential.
//
// Accounts have no password: the administrator enters an email, Cloudflare
// Access checks that address with a one-time code, and the app matches it to
// the account. So there is nothing secret in this text - it can be sent over
// anything - and it must never imply a password exists to be typed. The
// previous version carried a temporary password, which is why it could only
// be built at creation time and why two accounts sat unusable for eleven days
// when nobody delivered it.
export function buildInviteMessage({ loginUrl, email, displayName } = {}) {
  const address = String(email || "").trim();
  if (!address) return "";

  const url = String(loginUrl || "").trim() || "https://app.agxtrade.com";
  const name = String(displayName || "").trim();

  return [
    name ? `Hi ${name},` : "Hi,",
    "",
    "Your AGX account is ready. There is no password to remember.",
    "",
    `1. Go to: ${url}`,
    `2. Enter this email address: ${address}`,
    "3. You'll be emailed a one-time code - enter it, and you're in.",
    "",
    "Use the same address every time; that is what identifies your account.",
  ].join("\n");
}
