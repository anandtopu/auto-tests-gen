#!/usr/bin/env bash
# LLM Runner port — OpenHands as a delegated provider (multi-LLM story 2.4).
# EXPERIMENTAL: requires AIQE_OPENHANDS_PROVIDER=1 (llm_runner refuses first).
#
# COMPLETION class, and that is a correction to the original design. A
# delegated conversation runs the agent in ITS OWN sandbox, so anything it
# writes never lands in workspace/tests/<repo> where the gate looks. Closing
# that gap would need either the agent pushing its own branch — which the
# constitution forbids, the gate is the only push path — or a fetch-back
# channel nobody asked for. So this adapter does what a completion provider
# does: send the assembled prompt, harvest the FINAL AGENT MESSAGE, and let
# the harness materialize artifacts from the contract (derived_writes.py).
# Having OpenHands author tests is still fully supported — as a TRIGGER that
# runs the pipeline, where the gate still commits.
#
# Cost: a conversation's spend lands on the OpenHands account, and no usage is
# reported back to us. We therefore emit NO usage and NO total_cost_usd, and
# the platform labels the phase `unknown` rather than 0 — an invented 0 would
# understate a real bill, which is the one thing the cost view may not do.
#
# Optionality: AIQE_OPENHANDS=off|auto|required governs the OPTIONAL trigger
# path, where an outage is `degraded`. Choosing openhands as the LLM provider
# makes it load-bearing for that run BY CONSTRUCTION, so here an outage is a
# failed phase — never a silent reroute to a paid provider.
#
# Verbs: run_phase <model> <max_turns> <allowed_tools> <out_json> (prompt on
# stdin) · capabilities · check. Unknown verb 64.
# Config: OPENHANDS_URL / OPENHANDS_API_KEY (as everywhere else) plus
# OPENHANDS_PHASE_TIMEOUT (seconds, default 900) and OPENHANDS_POLL_SECONDS
# (default 10).
set -euo pipefail
VERB=${1:?verb}; shift || true
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"

case "$VERB" in
  run_phase)
    MODEL=${1:-}; TURNS=${2:-1}; TOOLS=${3:-}; OUT_JSON=${4:?out_json}
    [ "${AIQE_OPENHANDS_PROVIDER:-}" = "1" ] || {
      echo "PROVIDER_REFUSED: openhands-as-provider is experimental — set" \
           "AIQE_OPENHANDS_PROVIDER=1 to opt in. No silent fallback." >&2
      exit 1; }
    # `AIQE_OPENHANDS=off` means never contact it. Selecting it as the LLM
    # provider says the opposite; rather than pick a winner silently, refuse
    # and make the operator resolve the contradiction.
    # ROOT goes through the ENVIRONMENT, never interpolated into the python
    # source: on Windows it is a backslash path and `'C:\Users\...'` in a
    # string literal is a broken unicode escape (\U). That failure was
    # invisible because the fallback below swallowed it.
    OH_MODE=$(AIQE_OH_ROOT="$ROOT" python3 -c 'import os, sys
sys.path.insert(0, os.path.join(os.environ["AIQE_OH_ROOT"], "engine", "lib"))
import openhands_mode
print(openhands_mode.mode())' || echo auto)
    [ "$OH_MODE" != "off" ] || {
      echo "PROVIDER_REFUSED: AIQE_OPENHANDS=off says never contact OpenHands," \
           "but it is selected as the LLM provider — set AIQE_OPENHANDS=auto" \
           "or pick another provider." >&2
      exit 1; }
    # The heredoc owns stdin — park the assembled prompt first.
    TMP=$(mktemp); trap 'rm -f "$TMP"' EXIT
    cat > "$TMP"

    AIQE_OH_PROMPT="$TMP" AIQE_OH_OUT="$OUT_JSON" AIQE_OH_MODEL="$MODEL" \
    python3 - "$ROOT" <<'PY'
import json, os, sys, time
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")

root = sys.argv[1]
sys.path.insert(0, os.path.join(root, "engine", "lib"))
# The ADAPTER may import the client — that is what the ports/adapters boundary
# is for. `engine/` may not, and does not (constitution C7, pinned).
import openhands_client as oh                                    # noqa: E402

prompt = open(os.environ["AIQE_OH_PROMPT"], encoding="utf-8").read()
out_json = os.environ["AIQE_OH_OUT"]
model = os.environ.get("AIQE_OH_MODEL") or ""
timeout = float(os.environ.get("OPENHANDS_PHASE_TIMEOUT") or 900)
poll = max(2.0, float(os.environ.get("OPENHANDS_POLL_SECONDS") or 10))

TERMINAL_OK = {"finished", "completed", "stopped", "succeeded", "success"}
TERMINAL_BAD = {"error", "failed", "cancelled", "canceled", "timeout"}


def die(code, msg):
    print(msg, file=sys.stderr)
    sys.exit(code)


try:
    started = oh.start(prompt, title="AI-QE phase (LLM Runner)")
except Exception as e:                       # unreachable / unconfigured
    die(1, f"PROVIDER_UNREACHABLE: openhands ({e}) — check OPENHANDS_URL / "
           f"OPENHANDS_API_KEY, or switch provider in Settings. "
           f"No silent fallback.")

cid = started.get("conversation_id") or ""
url = started.get("url") or ""
if not cid:
    die(1, f"PROVIDER_BAD_RESPONSE: openhands started no conversation "
           f"({json.dumps(started)[:200]})")

# Record the launch BEFORE waiting. The webhook only arrives if OpenHands can
# reach a receiver we own; without this row a real conversation the user is
# paying for would be untrackable if we die mid-poll.
try:
    import openhands_events
    openhands_events.record_launch(cid, url=url, source="llm_runner",
                                   title="AI-QE phase")
except Exception:
    pass                                     # telemetry never fails a phase

print(f"[openhands] conversation {cid} — {url}", file=sys.stderr)

deadline = time.time() + timeout
state = ""
while time.time() < deadline:
    time.sleep(poll)
    try:
        st = oh.status(cid)
    except Exception as e:
        die(1, f"PROVIDER_UNREACHABLE: lost the conversation {cid} ({e}) — "
               f"it may still be running at {url}")
    state = str(st.get("execution_status") or st.get("status") or "").lower()
    if state in TERMINAL_BAD:
        die(1, f"PROVIDER_FAILED: openhands conversation {cid} ended "
               f"'{state}' — see {url}")
    if state in TERMINAL_OK:
        break
else:
    die(1, f"PROVIDER_TIMEOUT: openhands conversation {cid} still '{state}' "
           f"after {int(timeout)}s — raise OPENHANDS_PHASE_TIMEOUT or watch "
           f"it at {url}. Nothing was written.")

try:
    text = oh.final_message(cid)
except Exception as e:
    die(1, f"PROVIDER_BAD_RESPONSE: could not read conversation {cid} ({e})")
if not text.strip():
    die(1, f"PROVIDER_BAD_RESPONSE: openhands conversation {cid} finished "
           f"with no agent message — see {url}")

# Normalized result JSON. usage/total_cost_usd are ABSENT on purpose: the
# spend happened on the OpenHands account and is not reported to us. The cost
# view renders this `unknown`, which is the truth.
out = {"result": text, "num_turns": 1, "provider": "openhands",
       "model": model, "conversation_id": cid, "conversation_url": url}
open(out_json, "w", encoding="utf-8", newline="\n").write(json.dumps(out))
print(text)
PY
    ;;
  capabilities)
    # See the header: agentic phases are refused at config time because a
    # delegated sandbox is not our workspace.
    echo "completion"
    ;;
  check)
    python3 - "$ROOT" <<'PY'
import os, sys
sys.stdout.reconfigure(encoding="utf-8")
sys.stderr.reconfigure(encoding="utf-8")
sys.path.insert(0, os.path.join(sys.argv[1], "engine", "lib"))
import openhands_client as oh
h = oh.health()
if h.get("reachable"):
    print(f"openhands reachable at {h.get('endpoint', '')}")
else:
    print(f"openhands not reachable: {h.get('error', '')} {h.get('hint', '')}",
          file=sys.stderr)
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
