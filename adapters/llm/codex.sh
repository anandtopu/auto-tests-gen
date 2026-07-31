#!/usr/bin/env bash
# LLM Runner port — OpenAI Codex CLI adapter (multi-LLM story 2.3).
#
# AGENTIC class: `codex exec` runs a real tool loop (reads, edits, shell), so
# unlike the Ollama adapter this one can serve generate/validate. Two honest
# gaps versus the claude adapter, both surfaced rather than papered over:
#
#   1. NO PER-TOOL ALLOW-LIST. Codex governs capability by SANDBOX, not by an
#      allowedTools list, so this adapter MAPS our policy onto the closest
#      sandbox: a read-only phase (critic, planadversary, triage) gets
#      `read-only`; anything allowed to Write/Edit gets `workspace-write`.
#      That is coarser than claude's list — a phase permitted to write can
#      write anywhere in the workspace. It is still bounded by the workspace
#      and, for anything that reaches a repo, by the gate.
#   2. NO TURN CEILING. `codex exec` has no --max-turns equivalent, so the
#      per-phase ceiling in org-config is NOT enforced here. The result JSON
#      therefore reports turn_limit_enforced:false — telemetry must not claim
#      a ceiling that nothing applied. The budget ceiling (exit 77) is the
#      backstop that still holds.
#
# Cost: codex reports TOKENS, not dollars, so no total_cost_usd is emitted —
# the platform prices it from org-config `pricing:` and labels the figure
# `estimated` (~$). Inventing a dollar figure here would break the
# measured-vs-estimated rule.
#
# Model ids are configured, never guessed: llm.models_by_provider.codex maps
# each tier id. An unmapped tier reaches the CLI as a claude id and fails
# loudly — `llm_runner.py validate` catches it at config time first.
#
# Verbs: run_phase <model> <max_turns> <allowed_tools> <out_json> (prompt on
# stdin) · capabilities · check. Unknown verb 64; a missing/unauthenticated
# CLI exits 1 naming the fix — never a silent fallback to another provider.
#
# CLI-SHAPE ASSUMPTIONS (the parts a codex version bump could invalidate):
# `codex exec - ` reads the prompt from stdin, `--json` streams JSONL events,
# `--output-last-message FILE` captures the final message, `--sandbox` takes
# read-only|workspace-write. The event-stream parse is deliberately defensive
# (recursive scan for token counts, several message-field spellings) because
# that shape HAS moved between versions; the flags are not — an unknown flag
# makes the CLI exit non-zero and this adapter reports PROVIDER_FAILED with
# the CLI's own stderr, which names the flag.
set -euo pipefail
VERB=${1:?verb}; shift || true
CODEX_BIN="${CODEX_BIN:-codex}"

case "$VERB" in
  run_phase)
    MODEL=${1:?model}; TURNS=${2:-1}; TOOLS=${3:-}; OUT_JSON=${4:?out_json}
    command -v "$CODEX_BIN" >/dev/null 2>&1 || {
      echo "PROVIDER_UNAVAILABLE: codex CLI not found (\$CODEX_BIN=$CODEX_BIN)" \
           "— install it (npm i -g @openai/codex) or switch provider in" \
           "Settings. No silent fallback." >&2; exit 1; }

    # Policy -> sandbox. Write/Edit in the allow-list means the phase authors
    # files; everything else is an opinion-only phase and gets read-only.
    case "$TOOLS" in
      *Write*|*Edit*) SANDBOX=workspace-write ;;
      *)              SANDBOX=read-only ;;
    esac

    # The python heredoc below owns stdin — park the assembled prompt first.
    TMP=$(mktemp); trap 'rm -f "$TMP" "$TMP.events" "$TMP.last" "$TMP.err"' EXIT
    cat > "$TMP"

    set +e
    "$CODEX_BIN" exec \
      --model "$MODEL" \
      --sandbox "$SANDBOX" \
      --skip-git-repo-check \
      --json \
      --output-last-message "$TMP.last" \
      - < "$TMP" > "$TMP.events" 2>"$TMP.err"
    RC=$?
    set -e
    if [ "$RC" -ne 0 ]; then
      echo "PROVIDER_FAILED: codex exec exited $RC" >&2
      tail -5 "$TMP.err" >&2 || true
      exit "$RC"
    fi

    python3 - "$TMP.events" "$TMP.last" "$OUT_JSON" "$MODEL" "$TURNS" <<'PY'
import json, os, sys

events_f, last_f, out_json, model, turns = sys.argv[1:6]


def walk(node, hits):
    """Token counts move around between codex versions — scan for them rather
    than pinning one event shape, and keep the LAST sighting (cumulative)."""
    if isinstance(node, dict):
        for a, b in (("input_tokens", "output_tokens"),
                     ("prompt_tokens", "completion_tokens")):
            if a in node or b in node:
                hits.append((int(node.get(a) or 0), int(node.get(b) or 0),
                             int(node.get("cached_input_tokens")
                                 or node.get("cache_read_input_tokens") or 0)))
        for v in node.values():
            walk(v, hits)
    elif isinstance(node, list):
        for v in node:
            walk(v, hits)


hits, turns_seen, texts = [], 0, []
try:
    for line in open(events_f, encoding="utf-8", errors="replace"):
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except ValueError:
            continue                      # non-JSON chatter: not our business
        walk(ev, hits)
        kind = str(ev.get("type") or ev.get("msg", {}).get("type") or "")
        if "agent_message" in kind or kind.endswith("message"):
            turns_seen += 1
            for key in ("message", "text", "content", "last_agent_message"):
                val = (ev.get(key) if isinstance(ev.get(key), str)
                       else (ev.get("msg") or {}).get(key)
                       if isinstance(ev.get("msg"), dict) else None)
                if isinstance(val, str) and val.strip():
                    texts.append(val)
                    break
except OSError:
    pass

# The CLI's own final-message file is authoritative; the event scan is the
# fallback for a version that does not write it.
text = ""
if os.path.exists(last_f):
    text = open(last_f, encoding="utf-8", errors="replace").read().strip()
if not text and texts:
    text = texts[-1]
if not text:
    print("PROVIDER_BAD_RESPONSE: codex produced no final message "
          "(no --output-last-message file and no agent_message event)",
          file=sys.stderr)
    sys.exit(1)

tin, tout, tcache = hits[-1] if hits else (0, 0, 0)
# Normalized result JSON. total_cost_usd is ABSENT on purpose (see header):
# codex reports tokens, the platform prices them as an ESTIMATE.
out = {"result": text,
       "usage": {"input_tokens": tin, "output_tokens": tout,
                 "cache_read_input_tokens": tcache},
       "num_turns": turns_seen or 1,
       # The org-config ceiling did not govern this run — say so rather than
       # let a report imply the cap was applied.
       "max_turns_requested": int(turns), "turn_limit_enforced": False,
       "provider": "codex", "model": model}
open(out_json, "w", encoding="utf-8", newline="\n").write(json.dumps(out))
print(text)
PY
    ;;
  capabilities)
    echo "agentic"
    ;;
  check)
    command -v "$CODEX_BIN" >/dev/null 2>&1 || {
      echo "codex CLI not installed (\$CODEX_BIN=$CODEX_BIN) — npm i -g @openai/codex" >&2
      exit 1; }
    "$CODEX_BIN" --version >/dev/null 2>&1 || {
      echo "codex CLI present but not runnable" >&2; exit 1; }
    echo "codex CLI present ($("$CODEX_BIN" --version 2>/dev/null | head -1))"
    ;;
  *) echo "unknown verb $VERB"; exit 64 ;;
esac
