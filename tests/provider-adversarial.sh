#!/usr/bin/env bash
# Adversarial UAT for the LLM Runner port (multi-LLM story 5.3).
#
# Five attacks on the provider seam, each aimed at a failure that would be
# SILENT rather than loud — the only kind that actually hurts:
#
#   1 outage        an unreachable provider must END the phase, never reroute
#                   to a different (possibly paid) one
#   2 malformed     a provider returning garbage must leave NO result JSON;
#                   a phantom contract reads downstream as a phase that ran
#   3 no price      a provider with no price entry costs `unknown`, never 0 —
#                   a 0 understates a real bill
#   4 wrong class   a completion provider on an agentic phase is refused at
#                   CONFIG time, with the fix named
#   5 cache poison  provider A's cached result must never be replayed for
#                   provider B
#
# Run: make test-providers
set -u
ROOT="$(cd "$(dirname "$0")/.." && pwd)"; cd "$ROOT"
fail=0
TMPD=$(mktemp -d); trap 'rm -rf "$TMPD"' EXIT
check() { if [ "$1" = "$2" ]; then echo "PASS $3"; else echo "FAIL $3 ($2, want $1)"; fail=1; fi; }

# --- 1. provider outage mid-run -------------------------------------------
OUT="$TMPD/out1.json"
printf 'prompt' | AIQE_MOCK=0 OLLAMA_URL=http://127.0.0.1:9/v1 \
  bash adapters/llm/ollama.sh run_phase m 5 Read "$OUT" >"$TMPD/1.log" 2>&1
rc=$?
[ "$rc" -ne 0 ] && [ ! -f "$OUT" ] && grep -q "PROVIDER_UNREACHABLE" "$TMPD/1.log" \
  && grep -qi "no silent fallback" "$TMPD/1.log" && r=ok || r="rc=$rc"
check ok "$r" "outage fails loudly and writes nothing"

# --- 2. malformed provider response ---------------------------------------
# A server that answers 200 with a body the contract cannot come from.
python3 - "$TMPD" <<'PY' &
import json, sys
from http.server import BaseHTTPRequestHandler, HTTPServer
class H(BaseHTTPRequestHandler):
    def log_message(self, *a): pass
    def do_POST(self):
        self.rfile.read(int(self.headers.get("Content-Length") or 0))
        b = json.dumps({"unexpected": "shape"}).encode()
        self.send_response(200); self.send_header("Content-Length", str(len(b)))
        self.end_headers(); self.wfile.write(b)
    def do_GET(self): self.do_POST()
s = HTTPServer(("127.0.0.1", 0), H)
open(sys.argv[1] + "/port", "w").write(str(s.server_address[1]))
s.serve_forever()
PY
srv=$!
for _ in $(seq 1 50); do [ -s "$TMPD/port" ] && break; sleep 0.2; done
PORT=$(cat "$TMPD/port" 2>/dev/null || echo 0)
OUT="$TMPD/out2.json"
printf 'prompt' | AIQE_MOCK=0 OLLAMA_URL="http://127.0.0.1:$PORT/v1" \
  bash adapters/llm/ollama.sh run_phase m 5 Read "$OUT" >"$TMPD/2.log" 2>&1
rc=$?
kill $srv 2>/dev/null
[ "$rc" -ne 0 ] && [ ! -f "$OUT" ] && grep -q "PROVIDER_BAD_RESPONSE" "$TMPD/2.log" \
  && r=ok || r="rc=$rc file=$([ -f "$OUT" ] && echo present || echo absent)"
check ok "$r" "malformed response writes no phantom contract"

# --- 3. a provider with no price entry stays unknown ----------------------
r=$(python3 - <<'PY'
import pathlib, sys
sys.path.insert(0, "engine/lib")
import budget
cost, basis = budget.priced("no-such-vendor", "m",
                            {"input_tokens": 10_000, "output_tokens": 1_000})
print("ok" if (cost is None and basis == "unknown") else f"{cost}/{basis}")
PY
)
check ok "$r" "unpriced provider costs unknown, never 0"

# --- 4. completion provider on an agentic phase ---------------------------
r=$(AIQE_LLM_PROVIDER=ollama python3 -c "
import sys; sys.path.insert(0,'engine/lib')
import llm_runner
e = llm_runner.check_assignment('generate','ollama')
print('ok' if e and 'cannot run agentic phase' in e and 'phase_providers' in e else repr(e))" 2>&1 | tail -1)
check ok "$r" "agentic phase on a completion provider is refused with the fix"

# --- 5. cache poisoning across providers ----------------------------------
r=$(python3 - <<'PY'
import pathlib, sys, tempfile
sys.path.insert(0, "engine/lib")
import phase_cache
d = pathlib.Path(tempfile.mkdtemp())
prompt = d / "p.md"; prompt.write_text("same prompt", encoding="utf-8")
ctx = d / "c.md"; ctx.write_text("same context", encoding="utf-8")
# The wrapper passes PROVIDER:MODEL as the model component (both call sites).
a = phase_cache.key("triage", "claude:sonnet", str(prompt), [str(ctx)])
b = phase_cache.key("triage", "ollama:qwen", str(prompt), [str(ctx)])
print("ok" if a != b else "COLLISION")
PY
)
check ok "$r" "identical prompt on two providers does not share a cache key"

# The wrapper must actually USE that qualified key at both call sites — and
# qualify it by the MAPPED model, not the tier id. Keying on the tier meant
# re-pointing llm.models_by_provider kept the old key, so the next run replayed
# a result produced by a model that is no longer configured (review R3a).
n=$(grep -c '"${PROVIDER}:${FINAL_MODEL}"' engine/phases/run_phase.sh)
check 2 "$n" "run_phase qualifies both cache call sites by provider+mapped model"
n=$(grep -c '"${PROVIDER}:${MODEL}"' engine/phases/run_phase.sh || true)
check 0 "$n" "no cache call site keys on the unmapped tier id"

# --- 7. an unpriced provider must not silently disable the budget ----------
r=$(MAX_COST_USD_PER_RUN=1.00 AIQE_COST_LEDGER="$TMPD/led.tsv" python3 - "$TMPD" <<'PY'
import json, pathlib, sys
sys.path.insert(0, "engine/lib")
import budget
d = pathlib.Path(sys.argv[1])
budget.LEDGER = d / "led.tsv"
res = d / "r.json"
res.write_text(json.dumps({"provider": "mystery-vendor", "num_turns": 3,
                           "usage": {"input_tokens": 9_000_000,
                                     "output_tokens": 900_000}}), encoding="utf-8")
for _ in range(4):
    budget.record("generate", res)
state, msg = budget.enforceability()
print("ok" if state == "unenforceable" and "pricing:" in msg else f"{state}:{msg[:60]}")
PY
)
check ok "$r" "unpriced provider is reported as UNENFORCEABLE, not silently fine"

[ $fail -eq 0 ] && echo "provider adversarial UAT OK"
exit $fail
