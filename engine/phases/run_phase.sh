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
CONTEXT=""
for f in "$@"; do CONTEXT+=$'\n\n--- CONTEXT FILE: '"$f"$' ---\n'"$(cat "$f")"; done

cd "$WORKDIR"
claude -p "$(cat "$OLDPWD/$PROMPT")$CONTEXT" \
  --output-format json \
  --max-turns "$TURNS" \
  --allowedTools "$TOOLS" \
  --model "$MODEL" \
  --dangerously-skip-permissions \
  | tee "$OLDPWD/out/${PHASE}.json"
cd "$OLDPWD"
# Extract the trailing JSON contract the prompt requires the agent to print:
python3 engine/lib/extract_contract.py "out/${PHASE}.json" "engine/phases/contracts/${PHASE}.schema.json" \
  > "out/${PHASE}.contract.json"
