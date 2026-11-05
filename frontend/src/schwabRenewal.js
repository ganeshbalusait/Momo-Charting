// "Renew both" (2026-11-04): one click starts the Schwab Market Data login and
// queues Accounts & Trading behind it in the same window; the PC's local
// callback finishes each one, so nothing is pasted. Settings polls the status
// while it runs and reads progress from the login stamps changing.

export const SCHWAB_RENEW_POLL_MS = 3000;
export const SCHWAB_RENEW_GIVE_UP_MS = 15 * 60 * 1000;

const LABELS = { market_data: "Market Data", trading: "Accounts & Trading" };
const KEYS = { market_data: "marketData", trading: "trading" };

export const SCHWAB_RENEW_TIMED_OUT = "Nothing came back from Schwab, so the renewal stopped waiting. Click the renew button to try again.";
// Renewing by pasting (app.agxtrade.com, any computer - 2026-11-04): one
// Renew both click begins both logins; Settings walks Market Data then
// Accounts & Trading, each finished by pasting its address into its own box.
const BOX = { market_data: "Market Data callback URL", trading: "Optional Accounts & Trading callback URL" };
const COMPLETE = { market_data: "Complete Market Data OAuth", trading: "Complete Optional Trading OAuth" };

export function manualRenewal(payload) {
  const profiles = (payload && payload.profiles && payload.profiles.length ? payload.profiles : ["market_data"]).slice();
  const urls = { ...((payload && payload.authorizationUrls) || {}) };
  if (!urls[profiles[0]] && payload && payload.authorizationUrl) urls[profiles[0]] = payload.authorizationUrl;
  return { profiles, urls, done: [] };
}

export function manualRenewalNext(manual) {
  return manual ? manual.profiles.find((profile) => !manual.done.includes(profile)) || null : null;
}

// After a pasted login finished: the flow one step on, or the same flow when
// that profile was not part of it.
export function manualRenewalAfter(manual, profile) {
  if (!manual || !manual.profiles.includes(profile) || manual.done.includes(profile)) return manual;
  return { ...manual, done: [...manual.done, profile] };
}

export function manualRenewalText(manual) {
  if (!manual) return "";
  const next = manualRenewalNext(manual);
  const total = manual.profiles.length;
  if (!next) return total > 1 ? "✓ Both Schwab logins renewed." : `✓ Schwab ${LABELS[manual.profiles[0]]} renewed.`;
  const lead = manual.done.length ? "" : "This browser is not on the PC, so the login cannot finish by itself - you paste it. ";
  const step = total > 1 ? `Step ${manual.done.length + 1} of ${total}: ` : "";
  const finished = manual.done.length ? `✓ ${manual.done.map((profile) => LABELS[profile]).join(" and ")} renewed. ` : "";
  const open = manual.done.length ? `click Open ${LABELS[next]} login and sign in to Schwab` : "sign in to Schwab in the new window";
  return `${lead}${finished}${step}${open}. It ends on a page that will not load - copy that page's whole address `
    + `(https://127.0.0.1/?code=...), paste it into the ${BOX[next]} box below and click ${COMPLETE[next]}. `
    + "Do it straight away: the address works for only about 30 seconds.";
}

// A re-login moves the 7-day expiry; the 30-minute access refresh does not.
const loginStamp = (status, profile) => {
  const part = status && status[KEYS[profile]];
  return part ? part.refreshTokenExpiresAt || part.tokenSavedAt || null : null;
};

// What the PC's callback recorded for this caller's own login of that profile.
const resultOf = (status, profile, startedAt) => {
  const part = status && status[KEYS[profile]];
  const result = part && part.renewalResult;
  return result && Date.parse(result.at) >= Date.parse(startedAt) - 5000 ? result : null;
};

export function renewalBaseline(status, profiles) {
  const out = {};
  for (const profile of profiles || []) out[profile] = loginStamp(status, profile);
  return out;
}

export function renewalProgress(renewal, status) {
  const profiles = (renewal && renewal.profiles) || [];
  const button = profiles.length > 1 ? "Renew both" : "Renew Market Data";
  const done = profiles.filter((profile) => {
    const stamp = loginStamp(status, profile);
    const result = resultOf(status, profile, renewal.startedAt);
    return (Boolean(stamp) && stamp !== renewal.baseline[profile]) || Boolean(result && result.ok);
  });
  const failed = profiles.find((profile) => {
    const result = resultOf(status, profile, renewal.startedAt);
    return !done.includes(profile) && result && result.ok === false;
  });
  const both = profiles.length > 1;
  if (failed) {
    const error = resultOf(status, failed, renewal.startedAt).error;
    const reason = error ? `: ${String(error).replace(/\.?\s*$/, ".")}` : ".";
    return { done, finished: true, failed: true,
      text: `The Schwab ${LABELS[failed]} login did not finish${reason} Click ${button} to try again.` };
  }
  if (profiles.length && done.length === profiles.length) {
    return { done, finished: true, failed: false,
      text: both ? "✓ Both Schwab logins renewed." : `✓ Schwab ${LABELS[profiles[0]]} renewed.` };
  }
  if (done.length) {
    const next = profiles.find((profile) => !done.includes(profile));
    return { done, finished: false, failed: false,
      text: `✓ ${LABELS[done[0]]} renewed. Step 2 of 2: approve ${LABELS[next]} in the same window.` };
  }
  return { done, finished: false, failed: false,
    text: both
      ? `Step 1 of 2: log in to Schwab in the new window and approve ${LABELS[profiles[0]]}. ${LABELS[profiles[1]]} follows in the same window.`
      : `Log in to Schwab in the new window and approve ${LABELS[profiles[0]] || "the app"}.` };
}
