#!/usr/bin/env bash
# LLM Runner port — Message Batches adapter (batch slice 1).
#
# COMPLETION class, and that is a capability statement rather than a
# limitation we chose. A batch request is a SINGLE Messages call: the model can
# return tool_use blocks, but every turn needing a client tool result would be
# another batch submission at ~1h each. So generate/validate/reviewrepair are
# refused at config time by llm_runner (AGENTIC_PHASES), and the plan family
# works here exactly as it does for ollama — run_phase.sh concatenates the
# context into the prompt and derived_writes.py materializes the artifacts.
#
# WHY IT IS WORTH IT: batched requests cost 50% of the synchronous price.
# WHAT IT COSTS: latency. Most batches finish within an hour; the hard expiry
# is 24h. This is for work nobody is waiting on (see docs/prd-batch-api-cost-
# reduction.md §5) — it is NOT a drop-in replacement for the interactive path.
#
# Auth is NOT the Claude Code CLI's. `claude -p` commonly authenticates with a
# subscription; the Batch API needs ANTHROPIC_API_KEY. A missing key is a
# refusal naming the fix — never a silent fallback to the paid synchronous
# provider (constitution C12), because the operator turned this on to spend
# less and would otherwise pay full price without being told.
#
# Verbs: run_phase <model> <max_turns> <allowed_tools> <out_json> (prompt on
# stdin) · capabilities · check · tool_policy · usage. Unknown verb 64.
set -euo pipefail
VERB=${1:?verb}; shift || true
BASE="${ANTHROPIC_BASE_URL:-https://api.anthropic.com}"

case "$VERB" in
  usage)
    DAYS=${1:-}; case "$DAYS" in ''|*[!0-9]*) echo "usage window must be positive integer days" >&2; exit 64 ;; esac
    [ "$DAYS" -ge 1 ] 2>/dev/null || exit 64
    printf '%s\n' '{"schema":1,"state":"unavailable","provider":"batch","reason_code":"unsupported","reason":"batch spend is reported by the Anthropic org usage API, not per-batch"}'
    ;;
  run_phase)
    MODEL=${1:?model}; TURNS=${2:-1}; TOOLS=${3:-}; OUT_JSON=${4:?out_json}
    # The heredoc below owns stdin — park the assembled prompt in a file first.
    TMP=$(mktemp); trap 'rm -f "$TMP"' EXIT
    cat > "$TMP"
    python3 - "$BASE" "$MODEL" "$TMP" "$OUT_JSON" <<'PY'
import json
import os
import sys
import time
import urllib.error
import urllib.request

base, model, prompt_file, out_json = sys.argv[1:5]
base = base.rstrip("/")
prompt = open(prompt_file, encoding="utf-8").read()

key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
if not key:
    print("PROVIDER_UNCONFIGURED: the Message Batches API needs ANTHROPIC_API_KEY.\n"
          "  The Claude Code CLI's subscription login does NOT work here — batch is\n"
          "  an API-key feature. Set ANTHROPIC_API_KEY in .env, or switch provider\n"
          "  in Settings. No silent fallback to the paid synchronous path.",
          file=sys.stderr)
    sys.exit(1)

HEADERS = {"x-api-key": key,
           "anthropic-version": "2023-06-01",
           "content-type": "application/json"}

# custom_id is MANDATORY, not decorative: batch results may be returned in ANY
# order (the API docs' own example returns the second request first). Even a
# one-request batch is correlated by id rather than by position, so slice 2's
# fan-out cannot introduce an ordering bug this adapter never had.
CUSTOM_ID = os.environ.get("AIQE_BATCH_CUSTOM_ID") or f"aiqe-{os.getpid()}"

MAX_TOKENS = int(os.environ.get("AIQE_BATCH_MAX_TOKENS") or 8192)
POLL = max(1, int(os.environ.get("AIQE_BATCH_POLL_SECONDS") or 20))
MAX_WAIT = max(1, int(os.environ.get("AIQE_BATCH_MAX_WAIT_MIN") or 90)) * 60


def call(method, url, body=None):
    req = urllib.request.Request(url, method=method, headers=HEADERS,
                                 data=json.dumps(body).encode("utf-8") if body else None)
    with urllib.request.urlopen(req, timeout=120) as r:
        return json.load(r)


def unreachable(e, what):
    print(f"PROVIDER_UNREACHABLE: batch {what} failed ({e}). Check network and\n"
          f"  ANTHROPIC_API_KEY. No silent fallback to the synchronous provider.",
          file=sys.stderr)
    sys.exit(1)


# --- submit -----------------------------------------------------------------
body = {"requests": [{"custom_id": CUSTOM_ID,
                      "params": {"model": model,
                                 "max_tokens": MAX_TOKENS,
                                 "messages": [{"role": "user", "content": prompt}]}}]}
try:
    batch = call("POST", f"{base}/v1/messages/batches", body)
except (urllib.error.URLError, TimeoutError, OSError) as e:
    unreachable(e, "submit")

batch_id = batch.get("id")
if not batch_id:
    print(f"PROVIDER_BAD_RESPONSE: batch submit returned no id "
          f"({json.dumps(batch)[:200]})", file=sys.stderr)
    sys.exit(1)
# Announce the id BEFORE waiting. If we die in the poll loop the batch is still
# running and will still be billed — an operator who cannot name it cannot
# retrieve or cancel it.
print(f"[batch] submitted {batch_id} (custom_id={CUSTOM_ID}); "
      f"polling every {POLL}s, giving up after {MAX_WAIT // 60}m", file=sys.stderr)

# --- poll -------------------------------------------------------------------
deadline = time.monotonic() + MAX_WAIT
status = None
while time.monotonic() < deadline:
    try:
        batch = call("GET", f"{base}/v1/messages/batches/{batch_id}")
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        unreachable(e, f"poll of {batch_id}")
    status = batch.get("processing_status")
    if status == "ended":
        break
    time.sleep(POLL)
else:
    # C13: we do not know the outcome. Saying "failed" would assert the model
    # produced nothing; the batch is very likely still processing.
    print(f"BATCH_STILL_PROCESSING: {batch_id} had not ended after "
          f"{MAX_WAIT // 60}m (last status: {status}).\n"
          f"  NOTHING is known about this phase yet and the batch is still\n"
          f"  running — it will complete and be billed. Retrieve it later, or\n"
          f"  raise AIQE_BATCH_MAX_WAIT_MIN.", file=sys.stderr)
    sys.exit(1)

results_url = batch.get("results_url")
if not results_url:
    print(f"PROVIDER_BAD_RESPONSE: batch {batch_id} ended with no results_url",
          file=sys.stderr)
    sys.exit(1)

# --- retrieve ---------------------------------------------------------------
try:
    req = urllib.request.Request(results_url, headers=HEADERS)
    with urllib.request.urlopen(req, timeout=120) as r:
        raw = r.read().decode("utf-8")
except (urllib.error.URLError, TimeoutError, OSError) as e:
    unreachable(e, f"results download for {batch_id}")

entry = None
for line in raw.splitlines():
    line = line.strip()
    if not line:
        continue
    try:
        row = json.loads(line)
    except ValueError:
        continue                      # a malformed line is not our result
    if row.get("custom_id") == CUSTOM_ID:
        entry = row
        break

if entry is None:
    print(f"PROVIDER_BAD_RESPONSE: batch {batch_id} results contained no entry "
          f"for custom_id {CUSTOM_ID}", file=sys.stderr)
    sys.exit(1)

result = entry.get("result") or {}
rtype = result.get("type")

if rtype in ("expired", "canceled"):
    # These are NOT billed, and they are NOT a verdict about the phase. Saying
    # "the phase produced nothing" would be an established negative we have no
    # basis for (C13).
    print(f"BATCH_{rtype.upper()}: request {CUSTOM_ID} in batch {batch_id} was "
          f"{rtype} before the model saw it.\n"
          f"  It was NOT billed, and NOTHING is known about this phase — this is\n"
          f"  not a model refusal and not an empty answer. Re-run it.",
          file=sys.stderr)
    sys.exit(1)

if rtype != "succeeded":
    err = json.dumps(result.get("error") or result)[:300]
    print(f"BATCH_ERRORED: request {CUSTOM_ID} in batch {batch_id}: {err}",
          file=sys.stderr)
    sys.exit(1)

message = result.get("message") or {}
text = "".join(b.get("text") or "" for b in (message.get("content") or [])
               if isinstance(b, dict) and b.get("type") == "text")
if not text:
    print(f"PROVIDER_BAD_RESPONSE: batch {batch_id} returned no text content",
          file=sys.stderr)
    sys.exit(1)

usage = message.get("usage") or {}
# Normalized result JSON. total_cost_usd is deliberately ABSENT: the Batch API
# reports TOKENS, not dollars. budget.priced() turns those into an `estimated`
# figure from org-config `pricing:` (rendered with ~), and into `unknown` — never
# 0 — when the provider has no price entry. Emitting a cost here would claim a
# precision we do not have, and the 50% batch discount is a documented property
# applied to a list price, not a billed number we observed.
out = {"result": text,
       "usage": {"input_tokens": int(usage.get("input_tokens") or 0),
                 "output_tokens": int(usage.get("output_tokens") or 0),
                 "cache_read_input_tokens": int(usage.get("cache_read_input_tokens") or 0)},
       "num_turns": 1, "provider": "batch", "model": message.get("model") or model,
       "batch_id": batch_id}
open(out_json, "w", encoding="utf-8", newline="\n").write(json.dumps(out))
print(text)
PY
    ;;
  capabilities)
    # A batch request is one Messages call — no client-side tool loop, so the
    # agentic phases are refused at CONFIG time by llm_runner, with the fix named.
    echo "completion"
    ;;
  check)
    python3 - "$BASE" <<'PY'
import json
import os
import sys
import urllib.error
import urllib.request

base = sys.argv[1].rstrip("/")
key = os.environ.get("ANTHROPIC_API_KEY", "").strip()
if not key:
    print("batch provider not configured: ANTHROPIC_API_KEY is unset. The Claude "
          "Code CLI login does not work for the Batch API — it needs an API key.",
          file=sys.stderr)
    sys.exit(1)
req = urllib.request.Request(
    base + "/v1/messages/batches?limit=1",
    headers={"x-api-key": key, "anthropic-version": "2023-06-01"})
try:
    with urllib.request.urlopen(req, timeout=15) as r:
        data = json.load(r)
    n = len(data.get("data") or [])
    print(f"batch API reachable at {base} — listing works ({n} recent batch(es))")
except urllib.error.HTTPError as e:
    print(f"batch API refused at {base}: HTTP {e.code} — check ANTHROPIC_API_KEY",
          file=sys.stderr)
    sys.exit(1)
except Exception as e:
    print(f"batch API not reachable at {base}: {e}", file=sys.stderr)
    sys.exit(1)
PY
    ;;
  tool_policy)
    # A completion provider gets no tools at all; the harness materializes
    # artifacts from the contract. This can never be WIDER than what was asked.
    echo "none completion-provider allowedTools=${1:-}"
    ;;
  *) echo "unknown verb $VERB"; exit 64 ;;
esac
