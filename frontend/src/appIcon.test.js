// The AGX app icon (2026-11-13): drawing framings and the shipped PNG sizes.
import test from "node:test";
import assert from "node:assert/strict";
import fs from "node:fs";
import { fileURLToPath } from "node:url";
import { appIconSvg } from "../scripts/app-icon.svg.mjs";

const iconsDir = fileURLToPath(new URL("../public/icons/", import.meta.url));
const pngSize = (name) => {
  const buf = fs.readFileSync(iconsDir + name);
  assert.equal(buf.toString("ascii", 1, 4), "PNG", `${name} is a PNG`);
  return [buf.readUInt32BE(16), buf.readUInt32BE(20)];
};

test("rounded framing has the corner radius; iOS framing is full bleed", () => {
  assert.match(appIconSvg({ shape: "rounded" }), /rx="114" fill="url\(#bg\)"/);
  const square = appIconSvg({ shape: "square" });
  assert.match(square, /<rect x="0" y="0" width="512" height="512" fill="url\(#bg\)"\/>/);
  assert.doesNotMatch(square, /clip-path="url\(#tileClip\)"/);
});

test("maskable framing keeps the art inside the 80% safe zone", () => {
  assert.match(appIconSvg({ shape: "square", scale: 0.8 }), /scale\(0\.8\)/);
});

test("index.html carries the phone and tab icons inline, equal to the PNG files", () => {
  // An iPhone fetches the home-screen icon without the sign-in, so a /icons/
  // link came back as the login page and iOS drew a plain "A".
  const html = fs.readFileSync(fileURLToPath(new URL("../index.html", import.meta.url)), "utf8");
  const inline = (rel) => html.match(new RegExp(`<link rel="${rel}" href="data:image/png;base64,([^"]+)"`))?.[1];
  assert.equal(inline("apple-touch-icon"), fs.readFileSync(iconsDir + "apple-touch-icon.png").toString("base64"));
  assert.equal(inline("icon"), fs.readFileSync(iconsDir + "favicon-32.png").toString("base64"));
});

test("every icon the manifest and index.html name exists at its size", () => {
  const expected = {
    "icon-512.png": 512, "icon-192.png": 192, "icon-maskable-512.png": 512,
    "icon-maskable-192.png": 192, "apple-touch-icon.png": 180, "favicon-32.png": 32,
  };
  for (const [name, size] of Object.entries(expected)) assert.deepEqual(pngSize(name), [size, size], name);
});
