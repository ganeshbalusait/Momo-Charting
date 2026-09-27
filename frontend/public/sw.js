// Network-only service worker: makes AGX installable, and CANNOT go stale.
//
// History, because this file has been through both failure modes:
//
//   1. An early PWA build cached assets. That worker kept serving old bundles
//      after new ones shipped - a fresh bundle sat on the origin while the
//      browser showed days-old code no matter how often the trader reloaded
//      (observed 2026-08-28).
//   2. The fix was a tombstone worker that wiped every cache and unregistered
//      itself. It worked - and it also removed the last registered worker, so
//      Chrome stopped offering "Install AGX" in the address bar, which the
//      trader noticed on 2026-09-01: "chrome bar i dont see option to download
//      our app before it was there".
//
// Chrome will only offer installation when a service worker with a fetch
// handler controls the page. So this worker exists solely to satisfy that,
// and does the least possible work: EVERY request goes straight to the
// network. There is no cache, no cache API call, no stale-while-revalidate,
// nothing to serve an old asset from. Failure mode 1 is therefore not merely
// unlikely here, it is unreachable - you cannot serve a stale response you
// never stored.
//
// Freshness is handled where it belongs, outside this file: appVersion.js
// watches the entry bundle's content hash and offers "New version available".
// That check reads the network directly and does not depend on this worker.
//
// If you ever need to add caching here, do not. Ask why first: the cost of
// getting it wrong is the trader looking at yesterday's prices.

const VERSION = "agx-network-only-v1";

self.addEventListener("install", () => {
  // Take over immediately rather than waiting for every tab to close - a
  // trader with a chart open all day would otherwise never get the new worker.
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil((async () => {
    // Delete anything an older caching worker left behind. Without this, the
    // caches survive even though nothing reads them - and the next person to
    // add a cache-first handler would silently resurrect ancient assets.
    try {
      const keys = await caches.keys();
      await Promise.all(keys.map((key) => caches.delete(key)));
    } catch (err) {
      // A browser that denies the cache API is fine: there is nothing this
      // worker needs it for.
    }
    await self.clients.claim();
  })());
});

self.addEventListener("fetch", (event) => {
  // Straight to the network, always. This handler exists so the app is
  // installable; it deliberately adds no behaviour of its own. The catch
  // lets the browser show its normal offline error rather than a broken
  // half-response.
  event.respondWith(fetch(event.request).catch(() => Response.error()));
});
