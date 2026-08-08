#!/usr/bin/env bash
# LLM Runner port — Claude Code adapter (multi-LLM story 1.1). The DEFAULT
# provider: today's `claude -p` invocation extracted verbatim from
# run_phase.sh, so behavior is byte-identical while the port seam exists.
#
# Verbs:
#   run_phase <model> <max_turns> <allowed_tools> <out_json>
#       assembled prompt on STDIN (parked to a file first — a heredoc/pipe
#       must never race the CLI's own stdin handling); writes the result JSON
#       (claude's own shape IS the normalized shape: result/usage/num_turns/
#       total_cost_usd) to <out_json>, augmented with provider+model.
#   capabilities   prints "agentic" (full tool loop)
#   check          read-only reachability probe (CLI present + authenticated)
#
# Exit: run_phase propagates the CLI's exit; unknown verb 64.
set -euo pipefail
VERB=${1:?verb}; shift || true

case "$VERB" in
  usage)
    DAYS=${1:-}
    case "$DAYS" in ''|*[!0-9]*) echo "usage window must be positive integer days" >&2; exit 64 ;; esac
    [ "$DAYS" -ge 1 ] 2>/dev/null || { echo "usage window must be positive integer days" >&2; exit 64; }
    if [ -z "${ANTHROPIC_ADMIN_KEY:-}" ]; then
      printf '%s\n' '{"schema":1,"state":"unavailable","provider":"claude","reason_code":"credential-missing","reason":"ANTHROPIC_ADMIN_KEY is not configured"}'
      exit 0
    fi
    python3 - "$DAYS" <<'PY'
import datetime as dt, decimal, json, os, ssl, sys
import urllib.error, urllib.parse, urllib.request

days = int(sys.argv[1])
now_raw = os.environ.get("AIQE_USAGE_NOW", "").strip()
try:
    now = (dt.datetime.fromisoformat(now_raw.replace("Z", "+00:00"))
           if now_raw else dt.datetime.now(dt.timezone.utc))
    now = now.astimezone(dt.timezone.utc)
    end = now.replace(hour=0, minute=0, second=0, microsecond=0)
    start = end - dt.timedelta(days=days)
except (ValueError, OverflowError) as exc:
    print(json.dumps({"schema": 1, "state": "unavailable", "provider": "claude",
                      "reason_code": "window-invalid", "reason": str(exc)}))
    sys.exit(0)

def stamp(value):
    return value.isoformat(timespec="seconds").replace("+00:00", "Z")

base = os.environ.get("ANTHROPIC_ADMIN_BASE_URL", "https://api.anthropic.com").rstrip("/")
params = {"starting_at": stamp(start), "ending_at": stamp(end), "limit": "31"}
headers = {"x-api-key": os.environ["ANTHROPIC_ADMIN_KEY"],
           "anthropic-version": "2023-06-01",
           "User-Agent": "ai-qe-platform/1.0 (provider-usage-port)"}
context = None
if os.environ.get("AIQE_SSL_VERIFY", "1").strip() == "0":
    context = ssl._create_unverified_context()
total = decimal.Decimal("0")
seen_pages = set()
try:
    for _ in range(100):
        url = base + "/v1/organizations/cost_report?" + urllib.parse.urlencode(params)
        request = urllib.request.Request(url, headers=headers)
        with urllib.request.urlopen(request, timeout=30, context=context) as response:
            payload = json.load(response)
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise ValueError("cost report has no data array")
        for bucket in payload["data"]:
            for item in bucket.get("results") or []:
                if item.get("currency") != "USD":
                    raise ValueError("cost report contains a non-USD item")
                total += decimal.Decimal(str(item["amount"]))
        if not payload.get("has_more"):
            break
        page = payload.get("next_page")
        if not isinstance(page, str) or not page or page in seen_pages:
            raise ValueError("cost report pagination cursor is missing or repeated")
        seen_pages.add(page)
        params["page"] = page
    else:
        raise ValueError("cost report exceeded 100 pages")
except (urllib.error.URLError, TimeoutError, OSError, ValueError, KeyError,
        decimal.InvalidOperation, json.JSONDecodeError) as exc:
    print(json.dumps({"schema": 1, "state": "unavailable", "provider": "claude",
                      "reason_code": "provider-unavailable", "reason": type(exc).__name__}))
    sys.exit(0)

# Anthropic returns fractional cents as decimal strings. Decimal arithmetic and
# division by 100 preserve that contract without binary-float rounding.
amount = format(total / decimal.Decimal("100"), "f")
print(json.dumps({"schema": 1, "state": "available", "provider": "claude",
                  "window": {"starting_at": stamp(start), "ending_at": stamp(end),
                             "bucket_width": "1d"},
                  "cost": {"amount_usd": amount, "currency": "USD",
                           "basis": "provider-reported"},
                  "source": "anthropic-admin-cost-report"}, sort_keys=True))
PY
    ;;
  run_phase)
    MODEL=${1:?model}; TURNS=${2:?max_turns}; TOOLS=${3:?allowed_tools}
    OUT_JSON=${4:?out_json}
    TMP=$(mktemp); trap 'rm -f "$TMP"' EXIT
    cat > "$TMP"
    claude -p "$(cat "$TMP")" \
      --output-format json \
      --max-turns "$TURNS" \
      --allowedTools "$TOOLS" \
      --model "$MODEL" \
      --dangerously-skip-permissions \
      | tee "$OUT_JSON"
    # Augment with provider/model so telemetry stays provider-agnostic.
    # Best-effort: a malformed result is the phase's problem, not this step's.
    python3 - "$OUT_JSON" "$MODEL" <<'PY' || true
import json, sys
try:
    d = json.load(open(sys.argv[1], encoding="utf-8"))
    if isinstance(d, dict):
        d.setdefault("provider", "claude")
        d.setdefault("model", sys.argv[2])
        open(sys.argv[1], "w", encoding="utf-8", newline="\n").write(
            json.dumps(d))
except Exception:
    pass
PY
    ;;
  capabilities)
    echo "agentic"
    ;;
  tool_policy)
    # 5.1: what this adapter will ACTUALLY enforce for a given allow-list.
    # claude enforces it verbatim, so the answer is the input — printed anyway
    # so every agentic adapter answers the same question the same way, and a
    # read-only phase's policy is inspectable rather than assumed.
    POLICY=${1:-}
    case "$POLICY" in
      *Write*|*Edit*) echo "writable allowedTools=$POLICY" ;;
      *)              echo "readonly allowedTools=$POLICY" ;;
    esac
    ;;
  check)
    command -v claude >/dev/null 2>&1 || { echo "claude CLI not installed"; exit 1; }
    claude --version >/dev/null 2>&1 || { echo "claude CLI present but not runnable"; exit 1; }
    echo "claude CLI present ($(claude --version 2>/dev/null | head -1))"
    ;;
  *) echo "unknown verb $VERB"; exit 64 ;;
esac
