// PROJ-88: discount application
const { test } = require('node:test');
const assert = require('node:assert');
const BASE = process.env.API_BASE_URL || 'http://localhost:4600';

test('PROJ-88: applies % discount', async () => {
  const r = await fetch(`${BASE}/v1/orders/1/discounts`, { method: 'POST',
    headers: {'Content-Type':'application/json'}, body: JSON.stringify({ code: 'SAVE10', pct: 10 }) });
  assert.strictEqual(r.status, 200);
  const body = await r.json();
  assert.strictEqual(body.total, 90);
});
