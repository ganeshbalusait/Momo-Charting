// Masks the local part of an address for the admin lists.
//
// Those lists identify people by email, but an address usually spells out its
// owner's real name — so the Users, Device access and API-key panels show the
// masked form. Your own "My account" row keeps the full address, because that
// is what tells you which account you are signed in as.
//
// The domain stays visible: it is what makes two accounts for the same person
// (gmail vs yahoo) still tellable apart at a glance.
export function maskEmail(value) {
  const address = String(value || "").trim();
  if (!address) return "";

  const at = address.lastIndexOf("@");
  if (at < 1) return "••••";

  const local = address.slice(0, at);
  const domain = address.slice(at);

  // One real character is enough to scan a short list by; anything longer
  // starts giving the name back.
  return `${local[0]}••••${domain}`;
}
