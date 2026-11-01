// The few vitest `expect` matchers our tests use, on node:assert - the project runs
// `node --test` and has no vitest installed, so files importing "vitest" never ran.
import assert from "node:assert/strict";

export function expect(actual) {
  return {
    toBe: (want) => assert.ok(Object.is(actual, want), `expected ${String(actual)} to be ${String(want)}`),
    toEqual: (want) => assert.deepEqual(actual, want),
    toBeGreaterThan: (n) => assert.ok(actual > n, `expected ${actual} > ${n}`),
    toContain: (item) => assert.ok(actual.includes(item), `expected ${JSON.stringify(actual)} to contain ${JSON.stringify(item)}`),
    not: {
      toBe: (want) => assert.ok(!Object.is(actual, want), `expected ${String(actual)} not to be ${String(want)}`),
    },
  };
}
