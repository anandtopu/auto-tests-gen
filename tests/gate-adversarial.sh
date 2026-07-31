#!/usr/bin/env bash
# Adversarial gate regression (Review Pass 3, made permanent). Run: make test-gate
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT"; fail=0
# A FAILED setup must abort, never fall through: without the `||exit`s below a
# failed clone or cd left the shell in $ROOT, so the attack files ("planted
# secret", "out-of-scope src/") were written into THIS scaffold and the gate
# was pointed at it. The gate's exit-6 standalone-repo check caught it, but the
# harness must not need that backstop. (Seen for real when a concurrent job
# held workspace/tests.)
setup() {
  rm -rf workspace/tests
  bash adapters/mock/scm.sh clone_rw e2e-api-tests-1 \
       workspace/tests/e2e-api-tests-1 test/ADV-ai-qe || {
    echo "FAIL setup: clone_rw failed — refusing to run attacks in $ROOT"; exit 1; }
  cd workspace/tests/e2e-api-tests-1 || {
    echo "FAIL setup: cannot enter the clone — refusing to run attacks in $ROOT"
    exit 1; }
  [ "$PWD" != "$ROOT" ] || {
    echo "FAIL setup: still in $ROOT — refusing to attack the scaffold"; exit 1; }
}
run_gate() { AIQE_ROOT="$ROOT" timeout 60 bash "$ROOT/engine/gate/gate.sh" "$1" e2e-api-tests-1 >/tmp/gate-adv.log 2>&1; }
check() { local want=$1 got=$2 name=$3; [ "$got" = "$want" ] && echo "PASS $name" || { echo "FAIL $name (exit $got, want $want)"; fail=1; }; cd "$ROOT"; }

setup; echo 'const api_key = "sk-live-PLANTED";' > suites/orders/evil.spec.js
echo '{"file":"suites/orders/evil.spec.js","mapping":{"status":"confirmed"}}' >> catalog/generated.jsonl
run_gate ADV-SECRET; check 3 $? "secret-pattern blocked"

setup; mkdir -p src && echo x > src/app.js
run_gate ADV-SCOPE; check 2 $? "scope-violation blocked"

setup; echo 'const {test}=require("node:test");test("o",()=>{});' > suites/orders/unmapped.spec.js
run_gate ADV-UNMAPPED; check 4 $? "unmapped-test blocked"

setup
printf 'const {test}=require("node:test");const a=require("node:assert");\ntest("wrong", async()=>{const r=await fetch(`${process.env.API_BASE_URL}/v1/orders/1`);a.strictEqual(r.status,418);});\n' > suites/orders/failing.spec.js
echo '{"file":"suites/orders/failing.spec.js","mapping":{"status":"confirmed"}}' >> catalog/generated.jsonl
run_gate ADV-FAILING; check 5 $? "failing-test blocked (not committed)"

rm -rf workspace/tests; exit $fail
