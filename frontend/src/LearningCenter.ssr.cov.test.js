// Integration (2026-11-04 coverage): the real LearningCenter component,
// rendered to HTML with the real learnContent / learnScreenshots data and
// React's server renderer, then its screenshots served over a local HTTP server
// from frontend/public the way Vite and dist serve them. The existing
// learnContent tests check the DATA; this checks that the page actually turns
// that data into the steps, sections, buttons and images the trader sees.
//
// JSX needs a transform: vite's own esbuild bundles the component (with react,
// react-dom/server and lucide-react) into one CommonJS file in the OS temp dir.
import test, { after, before } from "node:test";
import assert from "node:assert/strict";
import { createRequire } from "node:module";
import { createServer } from "node:http";
import { readFile, rm, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

import { LEARN_ELSEWHERE, LEARN_SECTIONS, LEARN_STEPS } from "./learnContent.js";
import { LEARN_SCREENSHOTS, isScreenshotReady, screenshotById } from "./learnScreenshots.js";

const SRC = fileURLToPath(new URL(".", import.meta.url));
const PUBLIC = new URL("../public/", import.meta.url);
const requireHere = createRequire(import.meta.url);
const esbuild = createRequire(requireHere.resolve("vite"))("esbuild");
const bundlePath = path.join(tmpdir(), `agx-learn-ssr-${process.pid}.cjs`);

let html = "";
let server;
let base;

const escapeHtml = (text) => String(text).replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;").replace(/'/g, "&#x27;");
const count = (pattern) => (html.match(pattern) || []).length;

before(async () => {
  const built = await esbuild.build({
    stdin: {
      contents: [
        'import { createElement } from "react";',
        'import { renderToStaticMarkup } from "react-dom/server";',
        'import LearningCenter from "./LearningCenter.jsx";',
        "export const render = (props) => renderToStaticMarkup(createElement(LearningCenter, props));",
      ].join("\n"),
      resolveDir: SRC,
      loader: "jsx",
      sourcefile: "learn-ssr-entry.jsx",
    },
    bundle: true,
    platform: "node",
    format: "cjs",
    jsx: "automatic",
    write: false,
    logLevel: "silent",
    define: { "process.env.NODE_ENV": '"production"' },
  });
  await writeFile(bundlePath, built.outputFiles[0].text);
  html = createRequire(import.meta.url)(bundlePath).render({ onNavigate: () => {} });

  server = createServer(async (request, response) => {
    try {
      const body = await readFile(new URL(`.${new URL(request.url, "http://x").pathname}`, PUBLIC));
      response.writeHead(200, { "content-type": request.url.endsWith(".png") ? "image/png" : "application/octet-stream" });
      response.end(body);
    } catch {
      response.writeHead(404);
      response.end();
    }
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  base = `http://127.0.0.1:${server.address().port}`;
});

after(async () => {
  await rm(bundlePath, { force: true });
  if (server) await new Promise((resolve) => server.close(resolve));
});

test("the Learn tab is selected and renders the eight numbered first-morning steps", () => {
  assert.match(html, /role="tablist" aria-label="Learn and Setup"/);
  assert.match(html, /<button class="learn-tab learn-tab-active" type="button" role="tab" aria-selected="true">Learn<\/button>/);
  assert.match(html, /<h2 id="learn-steps-heading">Your first 15 minutes<\/h2>/);
  assert.equal(count(/<article class="learn-step">/g), LEARN_STEPS.length);
  assert.equal(LEARN_STEPS.length, 8);
  for (const step of LEARN_STEPS) {
    assert.ok(html.includes(`<h3>${escapeHtml(step.title)}</h3>`), `step "${step.title}" is missing`);
  }
});

test("every 'How each part works' section is a collapsed details block with its summary", () => {
  assert.equal(count(/<details class="learn-section">/g), LEARN_SECTIONS.length);
  assert.equal(count(/<details class="learn-section" open/g), 0, "sections start collapsed");
  for (const section of LEARN_SECTIONS) {
    assert.ok(html.includes(`<span>${escapeHtml(section.title)}</span>`), `section "${section.title}" has no summary`);
  }
});

test("every 'Take me there' label is a real button", () => {
  const labels = [...LEARN_STEPS, ...LEARN_SECTIONS].filter((item) => item.goTo).map((item) => item.goToLabel);
  assert.ok(labels.length > 0);
  assert.equal(count(/<button class="learn-goto" type="button">/g), labels.length);
  for (const label of labels) {
    assert.ok(html.includes(`<button class="learn-goto" type="button">${escapeHtml(label)}</button>`), `no button "${label}"`);
  }
  for (const entry of LEARN_ELSEWHERE) {
    assert.ok(html.includes(`<button type="button">${escapeHtml(entry.goToLabel)}</button>`), `Everything else lacks "${entry.goToLabel}"`);
  }
});

test("ready screenshots render as images with their callout markers; pending ones never do", () => {
  const used = [...LEARN_STEPS, ...LEARN_SECTIONS].map((item) => item.screenshot).filter(Boolean);
  for (const id of used) {
    const shot = screenshotById(id);
    const tag = shot ? `src="/learn/${shot.file}"` : null;
    if (isScreenshotReady(id)) {
      assert.ok(html.includes(tag), `ready screenshot ${id} is not on the page`);
    } else if (tag) {
      assert.ok(!html.includes(tag), `pending screenshot ${id} rendered as a broken image`);
    }
  }
  assert.ok(count(/<span class="learn-shot-marker"/g) > 0, "callout markers are drawn over the images");
});

test("every ready screenshot is served as a PNG from /learn/", async () => {
  const ready = LEARN_SCREENSHOTS.filter((shot) => isScreenshotReady(shot.id));
  assert.ok(ready.length > 0);
  for (const shot of ready) {
    const response = await fetch(`${base}/learn/${shot.file}`);
    assert.equal(response.status, 200, `/learn/${shot.file} is missing`);
    assert.equal(response.headers.get("content-type"), "image/png");
    const bytes = new Uint8Array(await response.arrayBuffer());
    assert.deepEqual([...bytes.slice(1, 4)], [0x50, 0x4e, 0x47], `/learn/${shot.file} is not a PNG`);
  }
});
