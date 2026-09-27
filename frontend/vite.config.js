import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Path-aware cache headers for the preview server (:4173, the origin behind
// app.agxtrade.com). Vite preview serves EVERYTHING with Cache-Control:
// no-cache, so a phone re-validated (and on weak signal re-downloaded) the
// ~1.3MB content-hashed bundle on every visit - measured 2026-08-28 while
// chasing ">1min chart loads" that were really stale/slow bundle delivery.
// Hashed /assets/ files never change under the same name, so they are safe to
// cache forever; index.html and sw.js keep no-cache so a phone always learns
// about a new build (and the tombstone service worker) on the next visit.
const previewCacheHeaders = {
  name: "agx-preview-cache-headers",
  configurePreviewServer(server) {
    server.middlewares.use((req, res, next) => {
      const url = String(req.url || "");
      if (url.startsWith("/assets/")) {
        // sirv (vite's static server) sets its own Cache-Control AFTER this
        // middleware runs, so setting the header here is silently overwritten
        // (verified 2026-08-28: header stayed no-cache). Pin it by rewriting
        // any later Cache-Control set on this response.
        const originalSetHeader = res.setHeader.bind(res);
        res.setHeader = (name, value) => {
          // sirv emits bare "text/css" with NO charset. Desktop browsers
          // guess UTF-8; iPhone Safari decoded the stylesheet as Latin-1 and
          // the briefing bullet "▸" became three mojibake glyphs drawn
          // OVER the text (trader's screenshot, 2026-08-30). Pin the charset
          // on every text-ish type.
          if (
            String(name).toLowerCase() === "content-type" &&
            /^(text\/|application\/(javascript|json))/i.test(String(value)) &&
            !/charset/i.test(String(value))
          ) {
            return originalSetHeader("Content-Type", `${value}; charset=utf-8`);
          }
          if (String(name).toLowerCase() === "cache-control") {
            return originalSetHeader("Cache-Control", "public, max-age=31536000, immutable");
          }
          return originalSetHeader(name, value);
        };
        res.setHeader("Cache-Control", "public, max-age=31536000, immutable");
      } else {
        res.setHeader("Cache-Control", "no-cache");
      }
      next();
    });
  },
};

export default defineConfig({
  plugins: [react(), tailwindcss(), previewCacheHeaders],
  build: {
    // The watchdog runs `vite build --watch` so dist/ cannot drift from the
    // :5173 dev server. Vite empties outDir at the START of every build, which
    // meant :4173 - the origin app.agxtrade.com is served from - had NO
    // frontend for the ~45s each rebuild takes (measured 13:42:45 -> 13:43:30).
    // Keeping the previous build in place means the old bundle keeps serving
    // until the new index.html lands, so a rebuild is invisible to the phone.
    // Asset filenames are content-hashed, so superseded files are inert; they
    // accumulate until a manual `npm run build -- --emptyOutDir` prunes them.
    emptyOutDir: false,
  },
  server: {
    port: 5173,
    // Allows the JS Self-Profiling API (new Profiler(...)) in the dev app so
    // main-thread freezes can be stack-sampled in place. Dev server only.
    headers: {
      "Document-Policy": "js-profiling",
    },
    proxy: {
      "/api": "http://127.0.0.1:3001",
    },
  },
  // `vite preview` serves the built dist for the Cloudflare tunnel. It rejects
  // unknown Host headers by default, so the tunnelled hostname must be listed
  // or every request through app.agxtrade.com returns "Blocked request".
  // This affects the preview server only — the dev server on :5173 is untouched.
  preview: {
    port: 4173,
    // Bind IPv4 explicitly. Left to itself vite preview listens on [::1] only,
    // and the tunnel dials 127.0.0.1 — different addresses, so every request
    // fails to connect.
    host: "127.0.0.1",
    allowedHosts: ["app.agxtrade.com"],
  },
});
