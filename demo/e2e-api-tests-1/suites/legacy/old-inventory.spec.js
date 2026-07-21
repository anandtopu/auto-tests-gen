// legacy test with no matching endpoint anywhere — expected ORPHAN in bootstrap
const { test } = require('node:test');
test.skip('checks retired inventory endpoint', async () => {
  await fetch('http://localhost:4600/v0/inventory/sync');
});
