#!/usr/bin/env bash
# AIQE_MOCK=1 phase executor: deterministic stand-ins for LLM phases so the full
# pipeline (resolve → … → gate) is testable without API spend. Each stub performs the
# phase's real side effects (writes files) and emits the same JSON contract.
set -euo pipefail
PHASE=$1; KEY=$2; WORKDIR=$3
# Fan-out calls label their output so per-repo contracts don't overwrite each other
# (see run_phase.sh). The mock must use the same naming or the merge finds nothing.
OUT="${AIQE_PHASE_LABEL:-$PHASE}"
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
    # The mock knows exactly ONE spec, and it is an orders API spec. Under fan-out it
    # is asked once per resolved test repo, so for any other target it must report
    # honestly that it produced nothing rather than plant an API spec in a UI repo —
    # which is what a real LLM phase would decline to do too. This reproduces the
    # pre-fan-out demo outcome (the UI repo gates as NO_CHANGES) exactly.
    TARGET="${AIQE_TARGET_REPO:-e2e-api-tests-1}"
    if [ "$TARGET" != "e2e-api-tests-1" ]; then
      echo '{"tests":[],"open_questions":["mock phase has no exemplar spec for '"$TARGET"'"]}' \
        > "out/${OUT}.contract.json"
      echo "[mock] phase $PHASE ($TARGET) produced no tests"
      exit 0
    fi
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
    cat > "out/${OUT}.contract.json" << EOF
{"tests":[{"file":"suites/orders/${KEY}-discount-boundary.spec.js","name":"${KEY}: boundary","scenario_id":"${KEY}-S1","action":"created"}],"open_questions":[]}
EOF
    ;;
  analyze)
    cat > out/analyze.contract.json << 'EOF'
{"behaviors":[{"id":"B1","statement":"discount 1-90% accepted and total recalculated","source":"AC-1","layer":"api"},
              {"id":"B2","statement":"out-of-range discount rejected with 400","source":"AC-2","layer":"api"}],
 "requirements":[{"id":"R1","ears":"WHEN a discount over 90% is submitted, THE SYSTEM SHALL reject it and leave the order total unchanged","source":"AC-2"},
                 {"id":"R2","ears":"WHEN a valid discount is applied, THE SYSTEM SHALL recalculate the order total","source":"AC-1",
                  "ambiguity":"AC-3 does not define stacking behavior for multiple discounts"}],
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
    # Structured per SDD story 1.1: steps + verification make this a SPEC the
    # human signs and the gate can one day verify satisfaction against.
    echo '{"scenarios":[{"id":"'"${KEY}"'-S1","title":"boundary rejection >90%","layer":"api","target_repo":"e2e-api-tests-1","behavior_ref":"B2","requirement_refs":["R1"],"steps":{"given":"an order of $100 exists","when":"a 91% discount is POSTed","then":"the API responds 422 and the order total is unchanged"},"verification":["response status is 422","order total unchanged after rejection"],"data_needs":"d1"}],"open_questions":["stacking undefined"]}' > out/testplan.contract.json
    ;;
  planadversary)
    # The opponent: finds what the author MISSED. It never edits the plan.
    cat > out/planadversary.contract.json << EOF
{"gaps":[{"title":"discount applied to an already-discounted order (stacking)",
          "category":"boundary","severity":"high",
          "rationale":"AC-3 leaves stacking undefined and no scenario probes it"},
         {"title":"discount POST by a user without the orders:write scope",
          "category":"authz","severity":"high",
          "rationale":"the endpoint mutates an order but no scenario exercises authz"}],
 "verdict":"gaps_found","scenarios_reviewed":1}
EOF
    ;;
  planarbiter)
    # The arbiter: folds ACCEPTED gaps into the plan and re-emits the plan contract.
    mkdir -p testplans
    cat > "testplans/${KEY}.md" << EOF
# Test Plan — ${KEY}
## Existing Coverage (from catalog)
- PROJ-88 discount happy path already covered in e2e-api-tests-1.
## Scenarios
| ID | Title | Layer | Target repo | Behavior | Data |
| ${KEY}-S1 | boundary rejection >90% | api | e2e-api-tests-1 | B2 | d1 |
| ${KEY}-S2 | stacking on an already-discounted order | api | e2e-api-tests-1 | B1 | d2 |
| ${KEY}-S3 | discount POST without orders:write scope | api | e2e-api-tests-1 | B2 | d3 |
## Adversarial review
Two gaps raised by the plan adversary were accepted (stacking, authz); see §Open Questions.
## Open Questions
- AC-3 stacking behavior undefined.
EOF
    cat > out/planarbiter.contract.json << EOF
{"scenarios":[{"id":"${KEY}-S1","title":"boundary rejection","layer":"api","target_repo":"e2e-api-tests-1","behavior_ref":"B2","data_needs":"d1"},
              {"id":"${KEY}-S2","title":"stacking on an already-discounted order","layer":"api","target_repo":"e2e-api-tests-1","behavior_ref":"B1","data_needs":"d2"},
              {"id":"${KEY}-S3","title":"discount POST without orders:write scope","layer":"api","target_repo":"e2e-api-tests-1","behavior_ref":"B2","data_needs":"d3"}],
 "open_questions":["stacking undefined"],"accepted_gaps":2,"rejected_gaps":0}
EOF
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
