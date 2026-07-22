#!/usr/bin/env bash
# AIQE_MOCK=1 phase executor: deterministic stand-ins for LLM phases so the full
# pipeline (resolve → … → gate) is testable without API spend. Each stub performs the
# phase's real side effects (writes files) and emits the same JSON contract.
set -euo pipefail
PHASE=$1; KEY=$2; WORKDIR=$3
mkdir -p out
case "$PHASE" in
  triage)
    cat > out/triage.contract.json << EOF
{"impact":"create","areas":["orders discounts boundary"],"existing_tests":
 ["e2e-api-tests-1::suites/orders/discount.spec.js::PROJ-88: applies % discount"],
 "risk":"med","rationale":"contract adds validation path; boundary uncovered"}
EOF
    ;;
  generate)
    T="$WORKDIR/tests/e2e-api-tests-1"
    mkdir -p "$T/suites/orders" "$T/catalog"
    cat > "$T/suites/orders/${KEY}-discount-boundary.spec.js" << EOF
// ${KEY}: discount boundary validation (AI-generated)
const { test } = require('node:test');
const assert = require('node:assert');
const BASE = process.env.API_BASE_URL || 'http://localhost:4600';

test('${KEY}: rejects discount above 90%', async () => {
  const r = await fetch(\`\${BASE}/v1/orders/1/discounts\`, { method: 'POST',
    headers: {'Content-Type':'application/json'}, body: JSON.stringify({ code: 'MEGA', pct: 95 }) });
  assert.strictEqual(r.status, 400);
});

test('${KEY}: rejects zero-percent discount', async () => {
  const r = await fetch(\`\${BASE}/v1/orders/1/discounts\`, { method: 'POST',
    headers: {'Content-Type':'application/json'}, body: JSON.stringify({ code: 'ZERO', pct: 0 }) });
  assert.strictEqual(r.status, 400);
});
EOF
    # born-mapped sidecar (gate enforces this)
    cat >> "$T/catalog/generated.jsonl" << EOF
{"test_id":"e2e-api-tests-1::suites/orders/${KEY}-discount-boundary.spec.js::${KEY}","file":"suites/orders/${KEY}-discount-boundary.spec.js","title":"${KEY}: discount boundary","layer":"api","mapping":{"app_repos":["orders-api"],"feature":"${KEY}","confidence":1.0,"method":["born_mapped"],"status":"confirmed"}}
EOF
    cat > out/generate.contract.json << EOF
{"tests":[{"file":"suites/orders/${KEY}-discount-boundary.spec.js","name":"${KEY}: boundary","scenario_id":"${KEY}-S1","action":"created"}],"open_questions":[]}
EOF
    ;;
  analyze)
    cat > out/analyze.contract.json << 'EOF'
{"behaviors":[{"id":"B1","statement":"discount 1-90% accepted and total recalculated","source":"AC-1","layer":"api"},
              {"id":"B2","statement":"out-of-range discount rejected with 400","source":"AC-2","layer":"api"}],
 "open_questions":["AC-3 does not define stacking behavior for multiple discounts"]}
EOF
    ;;
  testplan)
    mkdir -p testplans
    cat > "testplans/${KEY}.md" << EOF
# Test Plan — ${KEY}
## Existing Coverage (from catalog)
- PROJ-88 discount happy path already covered in e2e-api-tests-1.
## Scenarios
| ID | Title | Layer | Target repo | Behavior | Data |
| ${KEY}-S1 | boundary rejection >90% | api | e2e-api-tests-1 | B2 | d1 |
## Open Questions
- AC-3 stacking behavior undefined.
EOF
    echo '{"scenarios":[{"id":"'"${KEY}"'-S1","title":"boundary rejection","layer":"api","target_repo":"e2e-api-tests-1","behavior_ref":"B2","data_needs":"d1"}],"open_questions":["stacking undefined"]}' > out/testplan.contract.json
    ;;
  testdata)
    mkdir -p "testdata/${KEY}"
    echo '{"cases":[{"code":"MEGA","pct":95,"expect":400},{"code":"ZERO","pct":0,"expect":400}]}' > "testdata/${KEY}/discount-cases.json"
    echo '{"fixtures":[{"canonical":"testdata/'"${KEY}"'/discount-cases.json","materialized":[]}],"strategy":"boundary+negative"}' > out/testdata.contract.json
    ;;
  validate)
    echo '{"passed":2,"failed":0,"repair_loops":0,"flaky_reruns":0}' > out/validate.contract.json
    ;;
  critic)
    # Advisory only. AIQE_MOCK_CRITIC_SCORE forces a score so the demo (and the
    # regression tests) can prove a terrible score still commits.
    cat > out/critic.contract.json << EOF
{"score":${AIQE_MOCK_CRITIC_SCORE:-0.86},"verdict":"accept","noise_count":0,
 "specs_reviewed":2,
 "findings":[{"file":"suites/orders/${KEY}-discount-boundary.spec.js","kind":"missing",
              "severity":"low","note":"no case for a discount above the cap"}],
 "rationale":"assertions check the discounted total, not just the status code"}
EOF
    ;;
  *) echo "no mock for $PHASE"; exit 1 ;;
esac
echo "[mock] phase $PHASE done"
