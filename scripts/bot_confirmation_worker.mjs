// Local deterministic evaluator. It sends no orders or model requests.
import readline from 'node:readline';
import { createRvolObservationTracker, scannerConfirmation } from '../frontend/src/scannerConfirmation.js';
import { scannerSetupTypes } from '../frontend/src/scannerSetupTypes.js';
// The backend observes every board, unlike a single scanner panel. Keep
// enough isolated ticker/board baselines for the entire configured universe.
const tracker = createRvolObservationTracker({ maxTickers: 20000 });
const lines = readline.createInterface({ input: process.stdin, crlfDelay: Infinity });
for await (const line of lines) {
  let request;
  try {
    request = JSON.parse(line);
    const at = Date.parse(request.publicationAt || ''), now = Date.parse(request.now || '');
    const signals = {}, contexts = {};
    if (Number.isFinite(at) && Number.isFinite(now) && at <= now && now-at <= 120000) {
      const seen = new Set();
      for (const row of request.rows || []) {
        const symbol = row?.symbol;
        if (typeof symbol !== 'string' || seen.has(symbol)) continue;
        seen.add(symbol);
        const observation = tracker.observe(row, at, request.board);
        const types = scannerSetupTypes(row, at);
        const confirmation = scannerConfirmation({ ...row, _rvolObservation: observation }, at, undefined, types);
        if (observation) contexts[symbol] = { version: 1, highestLevel: confirmation.level,
          setupTypes: types, setupCount: types.length, publicationAt: request.publicationAt,
          historyReady: Object.keys(observation.before).length > 0,
          increasing: confirmation.increasing, sqz: confirmation.sqz, fire: confirmation.fire,
          skittles: confirmation.skittles };
        if (!confirmation.level) continue;
        if (types.length < 3) continue;
        signals[symbol] = { version: 1, highestLevel: confirmation.level, setupTypes: types,
          setupCount: types.length, momMandatory: false, publicationAt: request.publicationAt,
          increasing: confirmation.increasing, sqz: confirmation.sqz, fire: confirmation.fire,
          skittles: confirmation.skittles, virtualOnly: true };
      }
    }
    process.stdout.write(JSON.stringify({ id: request.id, signals, contexts }) + '\n');
  } catch {
    process.stdout.write(JSON.stringify({ id: request?.id, error: 'Level evaluation failed' }) + '\n');
  }
}
