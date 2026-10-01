// Over the public tunnel (app.agxtrade.com) Cloudflare Access gates every
// /api/* call. When the Access session expires the edge answers with an HTML
// login page/redirect instead of JSON, while the installed PWA keeps painting
// its service-worker-cached shell - so the app looked alive and every panel
// just said "invalid or empty JSON". Name the real cause instead.
export const SESSION_EXPIRED_MESSAGE = "Session expired - reload this page and sign in again.";
export const INVALID_JSON_MESSAGE = "API returned an invalid or empty JSON response.";
export const API_UNAVAILABLE_MESSAGE = "API is unavailable right now (server error).";

const ACCESS_MARKERS = /cloudflareaccess\.com|cdn-cgi\/access\/login/i;
const HTML_START = /^\s*<(?:!doctype\b|html\b|\?xml)/i;

/**
 * Explain a response whose body failed JSON.parse.
 *
 * @param {{status?: number, url?: string, redirected?: boolean}} response
 * @param {string} raw - the response body text
 * @param {string} [origin] - current page origin; omit to read from window
 */
export function nonJsonResponseMessage(response, raw, origin) {
  const body = String(raw || "");
  const finalUrl = String(response?.url || "");
  const status = Number(response?.status || 0);

  // Strongest signal: the body or the final URL is literally the Access login.
  if (ACCESS_MARKERS.test(finalUrl) || ACCESS_MARKERS.test(body)) {
    return SESSION_EXPIRED_MESSAGE;
  }

  if (!HTML_START.test(body)) return INVALID_JSON_MESSAGE;

  // An HTML error page from the edge/origin is an outage, not an auth problem.
  if (status >= 500) return API_UNAVAILABLE_MESSAGE;

  const pageOrigin = origin
    ?? (typeof window !== "undefined" ? window.location?.origin : "");
  const offOrigin = Boolean(finalUrl) && Boolean(pageOrigin) && !finalUrl.startsWith(pageOrigin);

  // HTML that arrived via a redirect, from another origin, or with an auth
  // status is an interception - the request never reached our API.
  if (response?.redirected || offOrigin || status === 401 || status === 403) {
    return SESSION_EXPIRED_MESSAGE;
  }

  // Same-origin HTML with a normal status is usually the SPA shell being
  // served for an API path (routing/build issue), not an expired session.
  return INVALID_JSON_MESSAGE;
}

/**
 * Preserve the response status when JSON parsing fails. Chart and option-chain
 * loaders use it to distinguish a retryable 5xx/proxy interruption from a
 * permanent data error.
 */
export function nonJsonResponseError(response, raw, origin) {
  const error = new Error(nonJsonResponseMessage(response, raw, origin));
  const status = Number(response?.status || 0);
  if (Number.isFinite(status) && status > 0) error.httpStatus = status;
  return error;
}
