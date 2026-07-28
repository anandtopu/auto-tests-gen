#!/usr/bin/env bash
# claude -p wrapper: loads per-phase policy from org-config, archives transcripts.
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
TURNS=$(python3 -c "import yaml;print(yaml.safe_load(open('$CFG'))['phases']['$PHASE']['max_turns'])")
TOOLS=$(python3 -c "import yaml;print(yaml.safe_load(open('$CFG'))['phases']['$PHASE']['allowed_tools'])")
mkdir -p out
# Substitute the run key and (for fan-out calls) the single target test repo into the
# prompt template. TARGET_REPO is empty for whole-run phases; the prompts treat an
# empty value as "every resolved repo".
PROMPT_TEXT=$(sed -e "s/{{KEY}}/${KEY:-}/g" -e "s/{{TARGET_REPO}}/${AIQE_TARGET_REPO:-}/g" "$PROMPT")
CONTEXT=""
for f in "$@"; do CONTEXT+=$'\n\n--- CONTEXT FILE: '"$f"$' ---\n'"$(cat "$f")"; done

# Run from the engine root: prompts reference workspace/tests/, catalog/, testplans/
# relative to here (P3: cwd=workspace made every documented path miss).
claude -p "$PROMPT_TEXT$CONTEXT" \
  --output-format json \
  --max-turns "$TURNS" \
  --allowedTools "$TOOLS" \
  --model "$MODEL" \
  --dangerously-skip-permissions \
  | tee "out/${OUT}.json"
# Extract the trailing JSON contract the prompt requires the agent to print:
python3 engine/lib/extract_contract.py "out/${OUT}.json" "engine/phases/contracts/${PHASE}.schema.json" \
  > "out/${OUT}.contract.json"
