#!/usr/bin/env bash
# Catalog bootstrap for ONE test repo (architecture §5.9.2). Stages 1-3 automated;
# Stage 4 review queue exported for humans; Stage 5 publish via PR.
set -euo pipefail
TREPO=${1:?test_repo_name}
WS=workspace/bootstrap/$TREPO; mkdir -p "$WS" catalog/review
# Stage 0: clone test repo + harvest app-repo facts (contracts, route tables).
# The SCM adapter is resolved the same way the pipeline does (SCM_KIND -> org-config
# map; mock under AIQE_MOCK=1) — hardcoding github here broke Bitbucket/Stash estates.
# A failed clone is FATAL: continuing with no repo used to truncate an existing
# catalog/<repo>.jsonl to empty at Stage 4, which then stripped `covers` and
# silently unrouted the repo.
if [ "${AIQE_MOCK:-0}" = "1" ]; then
  SCM_SH=adapters/mock/scm.sh
else
  SCM_SH=$(python3 -c "import yaml;print(yaml.safe_load(open('registry/org-config.yaml'))['adapters']['scm']['${SCM_KIND:-github}'])")
fi
rm -rf "$WS/repo"
if ! bash "$SCM_SH" clone_ro "$TREPO" "$WS/repo" || [ ! -d "$WS/repo" ]; then
  echo "BOOTSTRAP_CLONE_FAILED: $TREPO via $SCM_SH — existing catalog left untouched" >&2
  exit 1
fi
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
# Stage 4: tier by confidence -> auto / review queue / orphan.
# Write-then-move: `> catalog/x.jsonl` truncates BEFORE tier.py runs, so a tier
# crash (e.g. malformed classified.json from a partial claude run, tolerated
# above) would empty an existing catalog and silently unroute the repo.
python3 catalog/bootstrap/tier.py "$WS" > "$WS/tiered.jsonl"
mv "$WS/tiered.jsonl" "catalog/${TREPO}.jsonl"
python3 catalog/review/export_review_queue.py "catalog/${TREPO}.jsonl" > "catalog/review/${TREPO}-queue.csv"
bash adapters/notify/slack.sh post "Catalog bootstrap ${TREPO}: $(wc -l < catalog/${TREPO}.jsonl) tests cataloged; review queue: catalog/review/${TREPO}-queue.csv" || true
# Stage 5: regenerate registry coverage maps + estate knowledge + query index
python3 catalog/bootstrap/regen_coverage.py
python3 bin/gen_agents_md.py
python3 catalog/bootstrap/index_db.py
echo "Bootstrap complete for ${TREPO}"
