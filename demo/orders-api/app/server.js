// Demo app-under-test: minimal Orders API
const http = require('http');
const orders = { "1": { id: "1", total: 100, discounts: [] } };
const server = http.createServer((req, res) => {
  const json = (code, body) => { res.writeHead(code, {'Content-Type':'application/json'}); res.end(JSON.stringify(body)); };
  const m = req.url.match(/^\/v1\/orders\/(\w+)(\/discounts)?$/);
  if (!m || !orders[m[1]]) return json(404, { error: 'not_found' });
  const order = orders[m[1]];
  if (req.method === 'GET' && !m[2]) return json(200, order);
  if (req.method === 'POST' && m[2]) {
    let b = ''; req.on('data', c => b += c);
    req.on('end', () => {
      const { code, pct } = JSON.parse(b || '{}');
      if (!code || typeof pct !== 'number' || pct <= 0 || pct > 90) return json(400, { error: 'invalid_discount' });
      order.discounts.push({ code, pct });
      order.total = Math.round(100 * (1 - pct / 100));
      return json(200, order);
    });
    return;
  }
  json(405, { error: 'method_not_allowed' });
});
server.listen(process.env.PORT || 4600, () => console.log('orders-api up on', process.env.PORT || 4600));
