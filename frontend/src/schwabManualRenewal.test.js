// Renewing Schwab by pasting (app.agxtrade.com, any computer - 2026-11-04):
// the steps Settings walks after one Renew both click.
import assert from "node:assert/strict";
import test from "node:test";

import { manualRenewal, manualRenewalAfter, manualRenewalNext, manualRenewalText } from "./schwabRenewal.js";

const START = {
  authorizationUrl: "https://schwab.example/md",
  authorizationUrls: { market_data: "https://schwab.example/md", trading: "https://schwab.example/tr" },
  profiles: ["market_data", "trading"],
  manualCallbackRequired: true,
};

test("Renew both from another computer keeps both login links, Market Data first", () => {
  const manual = manualRenewal(START);
  assert.deepEqual(manual.profiles, ["market_data", "trading"]);
  assert.equal(manual.urls.trading, "https://schwab.example/tr");
  assert.equal(manualRenewalNext(manual), "market_data");
});

test("step 1 says the login cannot finish by itself and names the Market Data box", () => {
  const text = manualRenewalText(manualRenewal(START));
  assert.match(text, /cannot finish by itself/);
  assert.match(text, /Step 1 of 2: sign in to Schwab in the new window/);
  assert.match(text, /Market Data callback URL box below and click Complete Market Data OAuth/);
  assert.match(text, /about 30 seconds/);
});

test("after Market Data is pasted, step 2 is the Accounts & Trading box", () => {
  const after = manualRenewalAfter(manualRenewal(START), "market_data");
  assert.equal(manualRenewalNext(after), "trading");
  const text = manualRenewalText(after);
  assert.match(text, /^✓ Market Data renewed\. Step 2 of 2: click Open Accounts & Trading login/);
  assert.match(text, /Optional Accounts & Trading callback URL box below and click Complete Optional Trading OAuth/);
  assert.doesNotMatch(text, /cannot finish by itself/);
});

test("both pasted: done", () => {
  const done = manualRenewalAfter(manualRenewalAfter(manualRenewal(START), "market_data"), "trading");
  assert.equal(manualRenewalNext(done), null);
  assert.equal(manualRenewalText(done), "✓ Both Schwab logins renewed.");
});

test("Trading pasted first still leaves Market Data as the next step", () => {
  const after = manualRenewalAfter(manualRenewal(START), "trading");
  assert.equal(manualRenewalNext(after), "market_data");
  assert.match(manualRenewalText(after), /^✓ Accounts & Trading renewed\. Step 2 of 2: click Open Market Data login/);
});

test("a server without the link list still gives the first login's link", () => {
  const manual = manualRenewal({ authorizationUrl: "https://schwab.example/md", profiles: ["market_data", "trading"] });
  assert.deepEqual(manual.urls, { market_data: "https://schwab.example/md" });
});

test("only Market Data configured: one step, no step numbers", () => {
  const manual = manualRenewal({ authorizationUrl: "https://schwab.example/md", profiles: ["market_data"] });
  assert.doesNotMatch(manualRenewalText(manual), /Step \d of/);
  assert.equal(manualRenewalText(manualRenewalAfter(manual, "market_data")), "✓ Schwab Market Data renewed.");
});

test("a login that is not part of the flow, or pasted twice, changes nothing", () => {
  const manual = manualRenewal({ authorizationUrl: "u", profiles: ["market_data"] });
  assert.equal(manualRenewalAfter(manual, "trading"), manual);
  const once = manualRenewalAfter(manual, "market_data");
  assert.equal(manualRenewalAfter(once, "market_data"), once);
  assert.equal(manualRenewalAfter(null, "market_data"), null);
  assert.equal(manualRenewalText(null), "");
});
