import test from 'node:test';
import assert from 'node:assert/strict';
import { loadPaperHistory } from './paperBotHistory.js';

test('paper history selects the requested account and range without substituting another account', async () => {
  const expected = { account: 'paper3', history: [{ day: '2026-10-30', rows: [{ pl: -25 }] }] };
  const fetcher = async (url, options) => {
    assert.match(url, /days=3$/);
    assert.equal(options.credentials, 'include');
    assert.ok(options.signal);
    return { ok: true, json: async () => ({ mirrors: [{ account: 'paper5' }, expected] }) };
  };
  assert.equal(await loadPaperHistory('paper3', 3, { fetcher }), expected);
});

test('missing accounts and HTTP failures are errors instead of permanent loading or fake empty trades', async () => {
  await assert.rejects(loadPaperHistory('paper3', 3, { fetcher: async () => ({ ok: true, json: async () => ({ mirrors: [] }) }) }), /unavailable/);
  await assert.rejects(loadPaperHistory('paper3', 3, { fetcher: async () => ({ ok: false, status: 503 }) }), /503/);
});

test('paper-history requests honor cancellation', async () => {
  const controller = new AbortController();
  const pending = loadPaperHistory('paper3', 3, { signal: controller.signal, fetcher: async (_, { signal }) => new Promise((_, reject) => {
    signal.addEventListener('abort', () => reject(signal.reason), { once: true });
  }) });
  controller.abort(new Error('request deadline'));
  await assert.rejects(pending, /request deadline/);
});
