// PROJ-61: order retrieval
const { test } = require('node:test');
const assert = require('node:assert');
const BASE = process.env.API_BASE_URL || 'http://localhost:4600';

test('PROJ-61: gets an order by id', async () => {
  const r = await fetch(`${BASE}/v1/orders/1`);
  assert.strictEqual(r.status, 200);
});
