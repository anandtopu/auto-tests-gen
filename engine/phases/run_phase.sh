#!/usr/bin/env bash
# claude -p wrapper: loads per-phase policy from org-config, archives transcripts.
# Usage: run_phase.sh <phase_name> <prompt_file> <workdir> [extra_context_file...]
set -euo pipefail
PHASE=$1; PROMPT=$2; WORKDIR=$3; shift 3
CFG=registry/org-config.yaml
MODEL=$(python3 -c "import yaml;c=yaml.safe_load(open('$CFG'));m=c['models'];print(m.get('$PHASE', m['generate']))")
TURNS=$(python3 -c "import yaml;print(yaml.safe_load(open('$CFG'))['phases']['$PHASE']['max_turns'])")
TOOLS=$(python3 -c "import yaml;print(yaml.safe_load(open('$CFG'))['phases']['$PHASE']['allowed_tools'])")
mkdir -p out
# Substitute the run key into the prompt template ({{KEY}} placeholders)
PROMPT_TEXT=$(sed "s/{{KEY}}/${KEY:-}/g" "$PROMPT")
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
  | tee "out/${PHASE}.json"
# Extract the trailing JSON contract the prompt requires the agent to print:
python3 engine/lib/extract_contract.py "out/${PHASE}.json" "engine/phases/contracts/${PHASE}.schema.json" \
  > "out/${PHASE}.contract.json"
