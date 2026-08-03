#!/usr/bin/env bash
# Catalog bootstrap for ONE test repo (architecture §5.9.2). Stages 1-3 automated;
# Stage 4 review queue exported for humans; Stage 5 publish via PR.
set -euo pipefail
TREPO=${1:?test_repo_name}
WS=workspace/bootstrap/$TREPO; mkdir -p "$WS"
# AIQE_MOCK resolved ONCE, the way engine/pipeline.sh does it: only the literal
# `1` used to count, so `true` or `yes` selected REAL adapters for somebody
# asking for mock. Unset still means real — `make bootstrap` passes nothing.
AIQE_MOCK_RESOLVED=0
case "$(printf '%s' "${AIQE_MOCK-0}" | tr 'A-Z' 'a-z')" in
  1|true|yes|on)  AIQE_MOCK_RESOLVED=1 ;;
  0|false|no|off) AIQE_MOCK_RESOLVED=0 ;;
  *) AIQE_MOCK_RESOLVED=1
     echo "WARNING: AIQE_MOCK='${AIQE_MOCK}' is not a recognized boolean - using MOCK adapters; nothing is cloned for real or billed." >&2 ;;
esac
# Catalog output resolves through app_paths (R12). This chain wrote to a
# hardcoded catalog/ regardless of AIQE_CATALOG_DIR / AIQE_STATE_DIR, so under a
# relocated state root it wrote into the image path it had been relocated OFF.
CAT_DIR=$(python3 -c "import sys;sys.path.insert(0,'engine/lib');import app_paths;print(app_paths.catalog_dir())")
# Stage 0: clone test repo + harvest app-repo facts (contracts, route tables).
# The SCM adapter is resolved the same way the pipeline does (SCM_KIND -> org-config
# map; mock under AIQE_MOCK=1) — hardcoding github here broke Bitbucket/Stash estates.
# A failed clone is FATAL: continuing with no repo used to truncate an existing
# catalog/<repo>.jsonl to empty at Stage 4, which then stripped `covers` and
# silently unrouted the repo.
if [ "$AIQE_MOCK_RESOLVED" = "1" ]; then
  SCM_SH=adapters/mock/scm.sh
else
  SCM_SH=$(python3 -c "import yaml;print(yaml.safe_load(open('registry/org-config.yaml'))['adapters']['scm']['${SCM_KIND:-github}'])")
fi
rm -rf "$WS/repo"
if ! bash "$SCM_SH" clone_ro "$TREPO" "$WS/repo" || [ ! -d "$WS/repo" ]; then
  echo "BOOTSTRAP_CLONE_FAILED: $TREPO via $SCM_SH — existing catalog left untouched" >&2
  exit 1
fi
# Stage 0b: the APP repos. harvest_facts.py reads contracts and route tables out
# of workspace/src/<repo>, and nothing here ever put them there — only
# bin/demo-bootstrap.sh did, by copying demo/. So the real chain harvested an
# empty fact set, correlate attributed nothing, every test tiered `orphan`,
# regen_coverage wrote `covers: []`, and the chain printed "Bootstrap complete"
# and exited 0. The duplicate chain hid it: only the demo copy was ever run.
#
# A single unreadable app repo is NOT fatal — an estate may hold repos this
# credential cannot see, and the tests covering the rest still deserve
# cataloging. It is reported, and harvest_facts refuses if it read NONE.
mkdir -p workspace/src
# name:artifact pairs — the reuse check below needs to know WHICH file makes a
# checkout useful here, not merely that a directory exists.
APP_REPOS=$(python3 -c "
import sys; sys.path.insert(0, 'engine/lib')
from registry import load_registry
for r in load_registry()['source_repositories']:
    a = r.get('contract') if r.get('type') == 'backend' else r.get('route_table')
    if a:
        print(r['name'] + ':' + a)")
for pair in $APP_REPOS; do
  app=${pair%%:*}; artifact=${pair#*:}
  # Reuse an existing clone only if the ARTIFACT IS THERE. `[ -d ]` treated the
  # mere presence of a directory as a usable checkout, so an interrupted clone,
  # a half-completed delete (Windows leaves empty dirs behind when a handle is
  # open) or an empty mount silently skipped the clone — and the contract that
  # attributes every test to this repo was never read. Harvesting then found
  # nothing, which is the exact input that turns a whole catalog `orphan`.
  [ -f "workspace/src/$app/$artifact" ] && continue
  bash "$SCM_SH" clone_ro "$app" "workspace/src/$app" >/dev/null 2>&1 \
    || echo "BOOTSTRAP_APP_CLONE_SKIPPED: $app — its contract cannot contribute attribution" >&2
done
python3 catalog/bootstrap/harvest_facts.py > "$WS/app-facts.json"
# Stage 1: EXTRACT
python3 catalog/bootstrap/extract.py "$WS/repo" "$TREPO" > "$WS/extracted.jsonl"
# Stage 2: CORRELATE
python3 catalog/bootstrap/correlate.py "$WS/extracted.jsonl" "$WS/app-facts.json" > "$WS/correlated.jsonl"
# Stage 3: CLASSIFY residue with LLM (claude -p per unresolved batch)
python3 catalog/bootstrap/split_residue.py "$WS/correlated.jsonl" "$WS"
# Mock mode skips the classifier: residue falls straight to the review/orphan
# tiers, which is what bin/demo-bootstrap.sh already documented as the demo
# behaviour. Without this guard, running the REAL chain under AIQE_MOCK=1 still
# billed a live account — the same "mock mode that is not mocked" shape as
# AIQE_MOCK itself, and the reason this chain could not be wired into a test.
# The stale file is REMOVED, not merely skipped: a classified.json left by an
# earlier real run would otherwise feed a mock run's tiering, and the result
# would look like the mock chain had classified something.
if [ "$AIQE_MOCK_RESOLVED" = "1" ]; then
  rm -f "$WS/classified.json"
elif [ -s "$WS/residue.jsonl" ]; then
  claude -p "$(cat catalog/bootstrap/classify-prompt.md)$(cat "$WS/residue.jsonl")" \
    --output-format json --max-turns 5 --allowedTools Read \
    --model claude-haiku-4-5-20251001 > "$WS/classified.json" || true
fi
# Stage 4: tier by confidence -> auto / review queue / orphan.
# Write-then-move: `> catalog/x.jsonl` truncates BEFORE tier.py runs, so a tier
# crash (e.g. malformed classified.json from a partial claude run, tolerated
# above) would empty an existing catalog and silently unroute the repo.
python3 catalog/bootstrap/tier.py "$WS" > "$WS/tiered.jsonl"
mkdir -p "$CAT_DIR/review"
mv "$WS/tiered.jsonl" "$CAT_DIR/${TREPO}.jsonl"
python3 catalog/review/export_review_queue.py "$CAT_DIR/${TREPO}.jsonl" > "$CAT_DIR/review/${TREPO}-queue.csv"
bash adapters/notify/slack.sh post "Catalog bootstrap ${TREPO}: $(wc -l < "$CAT_DIR/${TREPO}.jsonl") tests cataloged; review queue: $CAT_DIR/review/${TREPO}-queue.csv" || true
# Stage 5: regenerate registry coverage maps + estate knowledge + query index
python3 catalog/bootstrap/regen_coverage.py
python3 bin/gen_agents_md.py
python3 catalog/bootstrap/index_db.py
echo "Bootstrap complete for ${TREPO}"
