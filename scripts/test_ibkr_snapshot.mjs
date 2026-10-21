import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import test from 'node:test';
import vm from 'node:vm';

// Exercise the exact JavaScript sent by Python, with no browser or real fetch.
const source = readFileSync(new URL('../ibkr_options.py', import.meta.url), 'utf8');
const guard = source.match(/_GET = r"""([\s\S]*?)"""/);
const snapshot = source.match(/SNAPSHOT_FN = r"""([\s\S]*?)""" \+ _GET \+ r"""([\s\S]*?)"""/);
assert.ok(guard && snapshot, 'Locate the versioned snapshot function and GET guard');
const script = snapshot[1] + guard[1] + snapshot[2];

async function collect(responses, conids = [1], rounds = 8) {
  const requests = [];
  const context = {
    SPEC: { conids, fields: '84,86,6509', rounds, waitMs: 1500 },
    setTimeout: callback => callback(),
    fetch: async (url, options) => {
      assert.equal(options.method, 'GET');
      assert.equal(options.credentials, 'include');
      assert.ok(url.startsWith('/portal.proxy/v1/portal/iserver/marketdata/snapshot?'));
      assert.ok(requests.length < responses.length, 'Unexpected additional request');
      requests.push(url);
      return { status: 200, json: async () => responses[requests.length - 1] };
    },
  };
  const result = await vm.runInNewContext(`(${script})()`, context, { timeout: 1000 });
  return { result: JSON.parse(JSON.stringify(result)), requests };
}

for (const [label, blank] of [['blank', ''], ['null', null], ['prior-close prefix', 'C1.00'],
                             ['whitespace', ' '], ['non-finite', 'Infinity'], ['boolean', false]]) {
  test(`${label} bid/ask keep polling until numeric prices arrive`, async () => {
    const { result, requests } = await collect([
      [{ conid: 1, 84: blank, 86: blank, 6509: 'RpB' }],
      [{ conid: 1, 84: '1.00', 86: '1.05', 6509: 'RpB' }],
    ]);
    assert.equal(requests.length, 2);
    assert.equal(result[1]['84'], '1.00');
    assert.equal(result[1]['86'], '1.05');
  });
}

test('missing fields and split snapshots are merged before settling', async () => {
  const { result, requests } = await collect([
    [{ conid: 1 }], [{ conid: 1, 84: '1.00' }], [{ conid: 1, 86: '1.05' }],
  ]);
  assert.equal(requests.length, 3);
  assert.equal(result[1]['84'], '1.00');
  assert.equal(result[1]['86'], '1.05');
});

test('known zero bid settles for deterministic rejection without global cancellation', async () => {
  const { result, requests } = await collect([[{ conid: 1, 84: '0', 86: '0.05' }]]);
  assert.equal(requests.length, 1);
  assert.equal(result[1]['84'], '0');
  assert.ok(!script.includes('unsubscribe'));
});

test('terminal frozen or unsubscribed states do not poll indefinitely', async () => {
  for (const mode of ['ZBd', 'Y', 'N']) {
    const { requests } = await collect([[{ conid: 1, 6509: mode }]]);
    assert.equal(requests.length, 1);
  }
});

test('only pending requested contracts are polled', async () => {
  const { result, requests } = await collect([
    [{ conid: 1, 84: '1', 86: '1.05' }, { conid: 2, 84: '', 86: null },
     { conid: 999, 84: '2', 86: '2.05' }],
    [{ conid: 2, 84: '2', 86: '2.05' }],
  ], [1, 2]);
  assert.ok(requests[1].includes('conids=2&'));
  assert.equal(result[999], undefined);
});

test('unfilled quotes stop at the configured round limit', async () => {
  const { result, requests } = await collect(Array.from({ length: 8 }, () => [{ conid: 1, 84: '', 86: null }]));
  assert.equal(requests.length, 8);
  assert.equal(result[1]['84'], '');
});
