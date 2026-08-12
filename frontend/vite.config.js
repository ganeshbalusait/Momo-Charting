import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

export default defineConfig({
  plugins: [react(), tailwindcss()],
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
});
