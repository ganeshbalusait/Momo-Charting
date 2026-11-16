// The chart's REAL Project ROI code over fixed option chains, so
// momx/chart_plan.py can be held to it number for number.
//
//   node tests/chart_plan_truth.mjs <cases.json> <out.json>
//
// cases.json: [{name, chain (file, .json or .json.gz) | rows, side, expiry,
// strike, entry, spot, moves, defaultExpiry, now}]. Read only: it writes
// nothing but its own output file.
import fs from "node:fs";
import path from "node:path";
import zlib from "node:zlib";
import { fileURLToPath, pathToFileURL } from "node:url";

const here = path.dirname(fileURLToPath(import.meta.url));
const src = path.join(here, "..", "frontend", "src");
const load = (file) => import(pathToFileURL(path.join(src, file)).href);
const { buildProjectRoi, projectRoiChainEntry, projectRoiExpiries } = await load("chartProjectRoi.js");

const [casesFile, outFile] = process.argv.slice(2);
const readJson = (file) => {
  const raw = fs.readFileSync(file);
  return JSON.parse(file.endsWith(".gz") ? zlib.gunzipSync(raw).toString("utf8") : raw.toString("utf8"));
};
const chains = new Map();
const chainOf = (file) => {
  if (!chains.has(file)) chains.set(file, readJson(file));
  return chains.get(file);
};

const out = {};
for (const item of readJson(casesFile)) {
  const chain = item.chain ? chainOf(item.chain) : null;
  const rows = chain ? chain.selectedExpiryChainRows : item.rows;
  const now = new Date(item.now);
  // Exactly what ChartProjectRoiCard.jsx passes: the map holds only the chain's current ATM expiry.
  const atm = chain ? chain.currentAtm || {} : {};
  const defaultExpiry = item.defaultExpiry ?? String(atm.expiry || "").slice(0, 10);
  const moves = item.moves !== undefined ? item.moves
    : (defaultExpiry && Number(atm.expectedMove) > 0 ? { [defaultExpiry]: Number(atm.expectedMove) } : null);
  const result = buildProjectRoi({
    rows, spot: item.spot, defaultExpiry, expiryExpectedMoves: moves, now,
    plan: { side: item.side, expiry: item.expiry, strike: item.strike, entry: item.entry, entrySpot: item.spot },
  });
  const live = projectRoiExpiries(rows, now);
  const chartExpiry = live.some((e) => e.expiry === defaultExpiry) ? defaultExpiry : (live[0]?.expiry || "");
  const picked = chartExpiry ? projectRoiChainEntry({ rows, spot: item.spot, side: item.side, expiry: chartExpiry }) : null;
  out[item.name] = {
    found: Boolean(result.contract) && result.expiry === item.expiry && result.strike === Number(item.strike),
    side: result.side, expiry: result.expiry, strike: result.strike, entry: result.entry, entrySpot: result.entrySpot,
    expectedMove: result.expectedMove || null,
    delta: result.contract ? Number(result.contract.delta || 0) : null,
    gamma: result.contract ? Number(result.contract.gamma || 0) : null,
    stopPremium: result.stopPremium || null, stopStock: result.stopStock,
    targets: result.targets.map((t) => ({ key: t.key, label: t.label, stock: t.stock, reason: t.reason })),
    chartExpiry: chartExpiry || null, chartStrike: picked ? picked.strike : null,
  };
}
fs.writeFileSync(outFile, JSON.stringify(out));
