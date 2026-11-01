export async function loadPaperHistory(account, days, {
  fetcher = fetch, signal = AbortSignal.timeout(15000),
} = {}) {
  if (!['paper3', 'paper5'].includes(account)) throw new Error('Unknown paper account');
  const response = await fetcher('/api/momx-scanner/paper-mirror?days=' + encodeURIComponent(days), {
    credentials: 'include', cache: 'no-store', signal,
  });
  if (!response.ok) throw new Error('HTTP ' + response.status);
  const body = await response.json();
  const doc = Array.isArray(body?.mirrors) && body.mirrors.find(m => m.account === account);
  if (!doc) throw new Error(account + ' is unavailable from the paper service');
  return doc;
}
