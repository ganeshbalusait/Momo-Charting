// Unit: the crash screen (coverage 2026-11-04, group "shell"). node:test cannot
// load .jsx, so AppErrorBoundary.jsx is compiled here with the esbuild that
// ships inside Vite, then rendered with react-dom/server. Proves the fallback
// actually renders a readable alert with both ways out, and that "Reset to the
// default page" forgets ONLY the remembered page before reloading.
import test from "node:test";
import assert from "node:assert/strict";
import { readFileSync } from "node:fs";
import { createRequire } from "node:module";
import { pathToFileURL } from "node:url";

const requireFromFrontend = createRequire(new URL("../package.json", import.meta.url));
const esbuild = createRequire(requireFromFrontend.resolve("vite"))("esbuild");
const React = requireFromFrontend("react");
const { renderToStaticMarkup } = requireFromFrontend("react-dom/server");

async function loadBoundary() {
  const source = readFileSync(new URL("./AppErrorBoundary.jsx", import.meta.url), "utf8");
  let { code } = esbuild.transformSync(source, {
    loader: "jsx", format: "esm", jsx: "transform", jsxFactory: "React.createElement", jsxFragment: "React.Fragment",
  });
  code = code
    .replace(/from\s+["']react["']/, `from ${JSON.stringify(pathToFileURL(requireFromFrontend.resolve("react")).href)}`)
    .replace(/from\s+["']\.\/appCrashRecovery["']/, `from ${JSON.stringify(new URL("./appCrashRecovery.js", import.meta.url).href)}`);
  const module = await import(`data:text/javascript;base64,${Buffer.from(code).toString("base64")}`);
  return module.default;
}

const Boundary = await loadBoundary();

function crashed(message) {
  const boundary = new Boundary({ children: React.createElement("div", null, "chart") });
  boundary.state = Boundary.getDerivedStateFromError(new Error(message));
  return boundary.render();
}

function buttons(element) {
  const actions = React.Children.toArray(element.props.children).find((child) => child?.props?.className === "app-crash-actions");
  return React.Children.toArray(actions.props.children);
}

function withWindow(fn) {
  const calls = { removed: [], reloads: 0 };
  const previous = globalThis.window;
  globalThis.window = {
    localStorage: { removeItem: (key) => calls.removed.push(key) },
    location: { reload: () => { calls.reloads += 1; } },
  };
  try {
    fn();
  } finally {
    if (previous === undefined) delete globalThis.window;
    else globalThis.window = previous;
  }
  return calls;
}

test("with no error the workspace renders untouched", () => {
  const boundary = new Boundary({ children: React.createElement("main", { className: "workspace" }, "charts") });
  assert.equal(renderToStaticMarkup(boundary.render()), '<main class="workspace">charts</main>');
});

test("a render crash becomes a readable alert with both ways out", () => {
  const html = renderToStaticMarkup(crashed("Cannot read properties of undefined (reading 'bars')"));
  assert.match(html, /^<div class="app-crash-screen" role="alert">/);
  assert.match(html, /<h1>The workspace stopped rendering<\/h1>/);
  assert.match(html, /<p>Cannot read properties of undefined \(reading &#x27;bars&#x27;\)<\/p>/);
  assert.match(html, /<div class="app-crash-actions"><button type="button">Reset to the default page<\/button><button type="button">Reload<\/button><\/div>/);
});

test("Reset forgets only the remembered page, then reloads", () => {
  const [reset] = buttons(crashed("boom"));
  const calls = withWindow(() => reset.props.onClick());
  assert.deepEqual(calls, { removed: ["agenticActiveView"], reloads: 1 });
});

test("Reload reloads and forgets nothing", () => {
  const [, reload] = buttons(crashed("boom"));
  const calls = withWindow(() => reload.props.onClick());
  assert.deepEqual(calls, { removed: [], reloads: 1 });
});

test("the last crash stays readable for a remote diagnosis", () => {
  const boundary = new Boundary({});
  const previous = globalThis.window;
  globalThis.window = {};
  try {
    boundary.componentDidCatch(new Error("chart exploded"), { componentStack: "\n    at ChartPane" });
    assert.deepEqual(globalThis.window.__lastAppCrash, { message: "chart exploded", stack: "\n    at ChartPane" });
  } finally {
    if (previous === undefined) delete globalThis.window;
    else globalThis.window = previous;
  }
});
