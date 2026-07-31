#!/usr/bin/env bash
# LLM phase wrapper: loads per-phase policy from org-config, assembles the
# cache-ordered prompt, dispatches through the LLM Runner port
# (adapters/llm/<provider>.sh; claude is the default), archives transcripts.
# Usage: run_phase.sh <phase_name> <prompt_file> <workdir> [extra_context_file...]
#
# AIQE_PHASE_LABEL renames only the OUTPUT files (transcript + contract), never the
# policy lookup: a fan-out call like generate-e2e-ui-tests-1 must still resolve its
# model / max_turns / allowedTools from the `generate` phase entry. Without the split
# every per-repo call would overwrite out/generate.contract.json and only the last
# repo would survive into validate.
set -euo pipefail
PHASE=$1; PROMPT=$2; WORKDIR=$3; shift 3
OUT="${AIQE_PHASE_LABEL:-$PHASE}"
CFG=registry/org-config.yaml
MODEL=$(python3 -c "import yaml;c=yaml.safe_load(open('$CFG'));m=c['models'];print(m.get('$PHASE', m['generate']))")
# Degradation ladder (cost-reduction 5.3): near the budget envelope,
# NON-JUDGEMENT phases drop to the cheap tier (validate's — haiku) and, one
# rung later, scoped contexts halve. Judgement phases (testplan, the adversary
# pair, generate) never downgrade — they run full-quality or the run aborts at
# 100% via the existing exit-77 guard. Every rung is recorded for the run
# record: a reduced-cost result must say so.
GRADE=$(python3 engine/lib/budget.py grade 2>/dev/null || echo ok)
if [ "$GRADE" = "degrade_tier" ] || [ "$GRADE" = "degrade_context" ]; then
  case "$PHASE" in
    triage|analyze|testdata|critic|validate|resolve)
      CHEAP=$(python3 -c "import yaml;print(yaml.safe_load(open('$CFG'))['models']['validate'])")
      if [ "$MODEL" != "$CHEAP" ]; then
        echo "[budget] $GRADE: $PHASE runs on the cheap tier ($CHEAP)"
        MODEL="$CHEAP"
      fi ;;
  esac
  echo -e "${AIQE_PHASE_LABEL:-$PHASE}\t$GRADE" >> out/cost-degrade.tsv
fi
# (The context-halving rung is consulted by context_scope.py itself at $(CTX)
# time — an export here could never reach the parent shell's evaluation.)
TURNS=$(python3 -c "import yaml;print(yaml.safe_load(open('$CFG'))['phases']['$PHASE']['max_turns'])")
TOOLS=$(python3 -c "import yaml;print(yaml.safe_load(open('$CFG'))['phases']['$PHASE']['allowed_tools'])")
mkdir -p out
# LLM Runner port (multi-LLM story 1.1/1.2): resolve which provider serves
# this phase — AIQE_LLM_PROVIDER env > org-config llm.phase_providers >
# llm.provider > claude — and refuse an impossible assignment at CONFIG time
# (an agentic phase on a completion-only provider) with the fix named, never
# mid-run. NO silent fallback to another provider.
RUNNER_LINE=$(python3 engine/lib/llm_runner.py resolve "$PHASE") || {
  echo "PROVIDER_CONFIG: no usable LLM provider for $PHASE (see above)"
  exit 1
}
PROVIDER=$(printf '%s' "$RUNNER_LINE" | cut -f1)
RUNNER=$(printf '%s' "$RUNNER_LINE" | cut -f2)
# Content-addressed reuse: if this exact phase, model, prompt and context set has been
# run before, restore the result instead of paying for it again. The key is the whole
# input, so a stale hit is impossible; `generate`/`validate` are excluded because their
# product is files in the test repos, not the contract (see phase_cache.py).
if python3 engine/lib/phase_cache.py lookup "$PHASE" "$OUT" "${PROVIDER}:${MODEL}" "$PROMPT" \
     "${KEY:-}" "$@" 2>/dev/null; then
  echo "[cache] $PHASE reused a previous result for identical inputs (no LLM call)"
  exit 0
fi

# Prompt assembly is CACHE-ORDERED: stable bytes first, run-specific bytes last.
# {{KEY}} used to be substituted throughout the prompt, which put a run-unique value
# within the first few hundred tokens and made every invocation's prefix unique — no
# provider-side prompt cache can hit that. The template is now sent verbatim and the
# run's parameters are appended, so the prompt + shared context form a prefix that is
# byte-identical across runs of the same phase.
PROMPT_TEXT=$(cat "$PROMPT")
CONTEXT=""
for f in "$@"; do CONTEXT+=$'\n\n--- CONTEXT FILE: '"$f"$' ---\n'"$(cat "$f")"; done
# Run parameters go LAST, and resolve the placeholders the template still references.
CONTEXT+=$'\n\n--- RUN PARAMETERS ---\n'"KEY=${KEY:-}"
if [ -n "${AIQE_TARGET_REPO:-}" ]; then
  CONTEXT+=$'\n'"TARGET_REPO=${AIQE_TARGET_REPO}"
fi
CONTEXT+=$'\nWherever this prompt says {{KEY}} use the KEY above; wherever it says {{TARGET_REPO}} use TARGET_REPO (empty = every resolved test repo).\n--- END RUN PARAMETERS ---'

# Run from the engine root: prompts reference workspace/tests/, catalog/, testplans/
# relative to here (P3: cwd=workspace made every documented path miss).
# Dispatch through the LLM Runner port (multi-LLM story 1.1): the adapter
# receives the ASSEMBLED prompt on stdin (cache-ordering preserved byte for
# byte) plus this phase's policy, and writes the normalized result JSON to
# out/<OUT>.json. Model mapping (llm.models_by_provider) applies to the FINAL
# model — after any degradation-ladder downgrade above.
FINAL_MODEL=$(AIQE_PHASE_MODEL="$MODEL" AIQE_MAP_PROVIDER="$PROVIDER" python3 -c "
import os, sys; sys.path.insert(0, 'engine/lib')
import llm_runner
print(llm_runner.map_model(os.environ['AIQE_MAP_PROVIDER'], os.environ['AIQE_PHASE_MODEL']))")
printf '%s' "$PROMPT_TEXT$CONTEXT" | \
  bash "$RUNNER" run_phase "$FINAL_MODEL" "$TURNS" "$TOOLS" "out/${OUT}.json"
# Extract the trailing JSON contract the prompt requires the agent to print:
python3 engine/lib/extract_contract.py "out/${OUT}.json" "engine/phases/contracts/${PHASE}.schema.json" \
  > "out/${OUT}.contract.json"
# Record the result for identical future inputs. Never fatal: a cache write failure
# must not fail a phase that already succeeded.
python3 engine/lib/phase_cache.py store "$PHASE" "$OUT" "${PROVIDER}:${MODEL}" "$PROMPT" \
  "${KEY:-}" "$@" >/dev/null 2>&1 || true
