/* Service worker for the installable (PWA) build of the trading dashboard.
 *
 * Deliberately network-first for everything. This is a trading app: serving a
 * cached quote, chain or bundle would be worse than showing nothing. The cache
 * exists only so the window opens with a shell instead of Chrome's dinosaur
 * when the dev server / API is down, and so Chrome sees a fetch handler and
 * offers the install prompt.
 */

const CACHE = "agentic-shell-v1";

// Only these get written to the cache. Everything else (API, Vite dev
// modules, HMR, fonts) is passed straight through to the network.
const CACHEABLE = [/^\/$/, /^\/assets\//, /^\/icons\//, /^\/manifest\.webmanifest$/];

self.addEventListener("install", (event) => {
  event.waitUntil(self.skipWaiting());
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    (async () => {
      const names = await caches.keys();
      await Promise.all(names.filter((n) => n !== CACHE).map((n) => caches.delete(n)));
      await self.clients.claim();
    })(),
  );
});

function isCacheable(url) {
  return CACHEABLE.some((re) => re.test(url.pathname));
}

self.addEventListener("fetch", (event) => {
  const { request } = event;
  if (request.method !== "GET") return;

  const url = new URL(request.url);
  if (url.origin !== self.location.origin) return;
  if (url.pathname.startsWith("/api")) return; // never touch live data

  const navigation = request.mode === "navigate";
  if (!navigation && !isCacheable(url)) return;

  event.respondWith(
    (async () => {
      try {
        const response = await fetch(request);
        if (response.ok && (navigation || isCacheable(url))) {
          const copy = response.clone();
          const cache = await caches.open(CACHE);
          // Navigations are all served by the same index.html shell.
          cache.put(navigation ? "/" : request, copy);
        }
        return response;
      } catch (err) {
        const cached = await caches.match(navigation ? "/" : request);
        if (cached) return cached;
        throw err;
      }
    })(),
  );
});
