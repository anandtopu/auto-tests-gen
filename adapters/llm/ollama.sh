#!/usr/bin/env bash
# LLM Runner port — Ollama adapter (multi-LLM story 2.1).
#
# COMPLETION class: one chat completion over the pre-injected context. That is
# enough for every phase except generate/validate (see the capability matrix in
# docs/multi-llm-providers.md) because run_phase.sh concatenates all context
# into the prompt and — for the plan family — the harness materializes the
# artifacts from the contract (derived_writes.py).
#
# Talks OpenAI-compatible /v1/chat/completions over stdlib HTTP (no SDK, same
# rule as the Embed adapter), so it also serves LM Studio, vLLM, llama.cpp and
# any local gateway. Config: OLLAMA_URL (default http://localhost:11434/v1),
# OLLAMA_API_KEY (optional; local daemons need none).
#
# Verbs: run_phase <model> <max_turns> <allowed_tools> <out_json> (prompt on
# stdin) · capabilities · check. Unknown verb 64; unreachable daemon exits 1
# with PROVIDER_UNREACHABLE (never a silent fallback to a paid provider).
set -euo pipefail
VERB=${1:?verb}; shift || true
BASE="${OLLAMA_URL:-http://localhost:11434/v1}"

case "$VERB" in
  run_phase)
    MODEL=${1:?model}; TURNS=${2:-1}; TOOLS=${3:-}; OUT_JSON=${4:?out_json}
    # The heredoc below owns stdin — park the assembled prompt in a file first.
    TMP=$(mktemp); trap 'rm -f "$TMP"' EXIT
    cat > "$TMP"
    python3 - "$BASE" "$MODEL" "$TMP" "$OUT_JSON" <<'PY'
import json, sys, urllib.error, urllib.request

base, model, prompt_file, out_json = sys.argv[1:5]
url = base.rstrip("/") + "/chat/completions"
prompt = open(prompt_file, encoding="utf-8").read()
body = {"model": model, "stream": False,
        "messages": [{"role": "user", "content": prompt}]}
headers = {"Content-Type": "application/json"}
import os
if os.environ.get("OLLAMA_API_KEY"):
    headers["Authorization"] = "Bearer " + os.environ["OLLAMA_API_KEY"]
req = urllib.request.Request(url, json.dumps(body).encode("utf-8"),
                             headers=headers)
try:
    with urllib.request.urlopen(req, timeout=600) as r:
        data = json.load(r)
except (urllib.error.URLError, TimeoutError, OSError) as e:
    print(f"PROVIDER_UNREACHABLE: ollama at {base} ({e}) — start the daemon "
          f"(`ollama serve`), check OLLAMA_URL, or switch provider in "
          f"Settings. No silent fallback.", file=sys.stderr)
    sys.exit(1)

try:
    text = data["choices"][0]["message"]["content"]
except (KeyError, IndexError, TypeError):
    print(f"PROVIDER_BAD_RESPONSE: ollama returned no message content "
          f"({json.dumps(data)[:200]})", file=sys.stderr)
    sys.exit(1)

usage = data.get("usage") or {}
# Normalized result JSON — the shape every adapter must emit so telemetry
# stays provider-agnostic. total_cost_usd is deliberately ABSENT: local
# inference has no provider-reported cost, and inventing one would break the
# platform's measured-vs-estimated honesty rule (the Cost view renders local
# runs as "$0 (local)" with tokens still tracked — slice 3).
out = {"result": text,
       "usage": {"input_tokens": int(usage.get("prompt_tokens") or 0),
                 "output_tokens": int(usage.get("completion_tokens") or 0),
                 "cache_read_input_tokens": 0},
       "num_turns": 1, "provider": "ollama", "model": model}
open(out_json, "w", encoding="utf-8", newline="\n").write(json.dumps(out))
print(text)
PY
    ;;
  capabilities)
    echo "completion"
    ;;
  check)
    python3 - "$BASE" <<'PY'
import json, sys, urllib.error, urllib.request
base = sys.argv[1].rstrip("/")
try:
    with urllib.request.urlopen(base + "/models", timeout=10) as r:
        data = json.load(r)
    names = [m.get("id") for m in (data.get("data") or [])][:5]
    print(f"ollama reachable at {base} — models: {', '.join(n for n in names if n) or 'none pulled'}")
except Exception as e:
    print(f"ollama not reachable at {base}: {e}", file=sys.stderr)
    sys.exit(1)
PY
    ;;
  tool_policy)
    # 5.1: a completion provider gets no tools at all — the harness
    # materializes artifacts from the contract instead.
    echo "none completion-provider allowedTools=${1:-}"
    ;;
  *) echo "unknown verb $VERB"; exit 64 ;;
esac
