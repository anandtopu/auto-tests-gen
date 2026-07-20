#!/usr/bin/env bash
# Catalog bootstrap for ONE test repo (architecture §5.9.2). Stages 1-3 automated;
# Stage 4 review queue exported for humans; Stage 5 publish via PR.
set -euo pipefail
TREPO=${1:?test_repo_name}
WS=workspace/bootstrap/$TREPO; mkdir -p "$WS" catalog/review
# Stage 0: clone test repo + harvest app-repo facts (contracts, route tables)
bash adapters/scm/github.sh clone_ro "$TREPO" "$WS/repo" 2>/dev/null || true
python3 catalog/bootstrap/harvest_facts.py > "$WS/app-facts.json"
# Stage 1: EXTRACT
python3 catalog/bootstrap/extract.py "$WS/repo" "$TREPO" > "$WS/extracted.jsonl"
# Stage 2: CORRELATE
python3 catalog/bootstrap/correlate.py "$WS/extracted.jsonl" "$WS/app-facts.json" > "$WS/correlated.jsonl"
# Stage 3: CLASSIFY residue with LLM (claude -p per unresolved batch)
python3 catalog/bootstrap/split_residue.py "$WS/correlated.jsonl" "$WS"
if [ -s "$WS/residue.jsonl" ]; then
  claude -p "$(cat catalog/bootstrap/classify-prompt.md)$(cat "$WS/residue.jsonl")" \
    --output-format json --max-turns 5 --allowedTools Read \
    --model claude-haiku-4-5-20251001 > "$WS/classified.json" || true
fi
# Stage 4: tier by confidence -> auto / review queue / orphan
python3 catalog/bootstrap/tier.py "$WS" > "catalog/${TREPO}.jsonl"
python3 catalog/review/export_review_queue.py "catalog/${TREPO}.jsonl" > "catalog/review/${TREPO}-queue.csv"
bash adapters/notify/slack.sh post "Catalog bootstrap ${TREPO}: $(wc -l < catalog/${TREPO}.jsonl) tests cataloged; review queue: catalog/review/${TREPO}-queue.csv" || true
# Stage 5: regenerate registry coverage maps
python3 catalog/bootstrap/regen_coverage.py
echo "Bootstrap complete for ${TREPO}"
