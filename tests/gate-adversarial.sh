#!/usr/bin/env bash
# Adversarial gate regression (Review Pass 3, made permanent). Run: make test-gate
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT"; fail=0
# The transaction log is REDIRECTED for this suite. Nothing set AIQE_EVENTS_DIR,
# so every emit reached the estate's REAL audit log — the record an operator
# reads to see what happened here, and the input `make maintain` feeds to
# alert_rules.evaluate(), which counts events in a window and DELIVERS through
# the Notify port. A test suite could page somebody with its own traffic.
# Absolute + native: python resolves this variable, and a subprocess that has
# cd'd into a workspace checkout must not create a stray log there.
mkdir -p "$ROOT/out/test-events"
export AIQE_EVENTS_DIR="$(cd "$ROOT/out/test-events" && pwd -W 2>/dev/null || pwd)"
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

# The gate EXECUTES commands.{lint,test} from the repo's own .ai-qe/config.yaml.
# A run able to rewrite that file owns the one component holding push rights, so
# the config is off the writable scope AND the commands are read from the
# COMMITTED file. Both are checked: the attack must be refused with a scope
# violation, and the planted command must never run.
setup
rm -f /tmp/aiqe-gate-pwned.txt
sed -i 's|lint: "true"|lint: "echo PWNED > /tmp/aiqe-gate-pwned.txt; true"|' .ai-qe/config.yaml
run_gate ADV-CONFIG; check 2 $? "repo-config rewrite blocked"
if [ -f /tmp/aiqe-gate-pwned.txt ]; then
  echo "FAIL planted lint command EXECUTED"; fail=1; rm -f /tmp/aiqe-gate-pwned.txt
else
  echo "PASS planted lint command never executed"
fi

setup
printf 'const {test}=require("node:test");const a=require("node:assert");\ntest("wrong", async()=>{const r=await fetch(`${process.env.API_BASE_URL}/v1/orders/1`);a.strictEqual(r.status,418);});\n' > suites/orders/failing.spec.js
echo '{"file":"suites/orders/failing.spec.js","mapping":{"status":"confirmed"}}' >> catalog/generated.jsonl
run_gate ADV-FAILING; check 5 $? "failing-test blocked (not committed)"

# A dry run that is not dry. AIQE_GATE_CHECK_ONLY was compared against the
# literal "1", so `=true` fell through and the gate COMMITTED AND PUSHED for an
# operator who asked it not to. Unlike AIQE_MOCK the safe direction here is
# check-only: the two outcomes are "a run that wrote nothing" and "a commit in
# somebody's repository".
setup
printf 'const {test}=require("node:test");
test("ok", async()=>{});
' > suites/orders/dry.spec.js
echo '{"file":"suites/orders/dry.spec.js","mapping":{"status":"confirmed"}}' >> catalog/generated.jsonl
before=$(git rev-parse HEAD)
AIQE_GATE_CHECK_ONLY=true run_gate ADV-DRYRUN; rc=$?
check 0 $rc "check-only honours 'true'"
cd "$ROOT/workspace/tests/e2e-api-tests-1" 2>/dev/null || cd "$ROOT"
after=$(git rev-parse HEAD 2>/dev/null || echo "$before")
if [ "$before" = "$after" ]; then echo "PASS 'true' dry run committed nothing"
else echo "FAIL 'true' dry run CREATED A COMMIT"; fail=1; fi
grep -q "WOULD_COMMIT" /tmp/gate-adv.log && echo "PASS reported WOULD_COMMIT"   || { echo "FAIL did not report WOULD_COMMIT"; fail=1; }
cd "$ROOT"

# An unusable value must also refuse to write, and say why.
setup
printf 'const {test}=require("node:test");
test("ok", async()=>{});
' > suites/orders/dry2.spec.js
echo '{"file":"suites/orders/dry2.spec.js","mapping":{"status":"confirmed"}}' >> catalog/generated.jsonl
before=$(git rev-parse HEAD)
AIQE_GATE_CHECK_ONLY=bogus run_gate ADV-DRYRUN2; rc=$?
check 0 $rc "unusable check-only value refuses to commit"
cd "$ROOT/workspace/tests/e2e-api-tests-1" 2>/dev/null || cd "$ROOT"
after=$(git rev-parse HEAD 2>/dev/null || echo "$before")
if [ "$before" = "$after" ]; then echo "PASS unusable value committed nothing"
else echo "FAIL unusable value CREATED A COMMIT"; fail=1; fi
cd "$ROOT"

rm -rf workspace/tests; exit $fail
