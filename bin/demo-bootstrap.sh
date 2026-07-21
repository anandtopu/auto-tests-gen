#!/usr/bin/env bash
# Run the catalog bootstrap stages against the in-repo demo estate — no credentials,
# no LLM (Stage 3 residue goes straight to review/orphan tiers, as designed).
# Usage: bin/demo-bootstrap.sh <test_repo_name>
set -euo pipefail
TREPO=${1:?test_repo_name}
WS="workspace/bootstrap/$TREPO"
mkdir -p "$WS" workspace/src catalog/review

# Wire demo repos into the paths the stages expect (real runs: Scm.clone_ro)
for r in orders-api web-storefront-ui catalog-api; do
  [ -d "demo/$r" ] && { rm -rf "workspace/src/$r"; cp -r "demo/$r" "workspace/src/$r"; }
done
rm -rf "$WS/repo"; cp -r "demo/$TREPO" "$WS/repo"

# Rebuild JIRA-keyed git history from each spec's header comment (nested .git dirs
# can't ship inside this scaffold) so correlate.py's git_history evidence works.
if [ ! -d "$WS/repo/.git" ]; then
  G() { git -C "$WS/repo" -c user.email=demo@ai-qe.local -c user.name=ai-qe-demo "$@"; }
  G init -q
  while IFS= read -r spec; do
    rel=${spec#"$WS/repo/"}
    msg=$(head -1 "$spec" | sed 's|^//[[:space:]]*||')
    G add "$rel"; G commit -qm "test: ${msg:-add $rel}"
  done < <(find "$WS/repo" -name '*.spec.*' | sort)
  G add -A; G commit -qm "chore: import remaining files" >/dev/null 2>&1 || true
fi

python3 catalog/bootstrap/harvest_facts.py > "$WS/app-facts.json"
python3 catalog/bootstrap/extract.py "$WS/repo" "$TREPO" > "$WS/extracted.jsonl"
python3 catalog/bootstrap/correlate.py "$WS/extracted.jsonl" "$WS/app-facts.json" > "$WS/correlated.jsonl"
python3 catalog/bootstrap/split_residue.py "$WS/correlated.jsonl" "$WS"
python3 catalog/bootstrap/tier.py "$WS" > "catalog/${TREPO}.jsonl"
python3 catalog/review/export_review_queue.py "catalog/${TREPO}.jsonl" > "catalog/review/${TREPO}-queue.csv"
python3 catalog/bootstrap/regen_coverage.py
python3 bin/gen_agents_md.py
echo "--- catalog/${TREPO}.jsonl ---"
python3 -c "
import json,sys
for l in open('catalog/${TREPO}.jsonl'):
    e=json.loads(l); m=e['mapping']
    print(f\"{m['status']:<12} conf={m['confidence']:<5} {e['title'][:40]:<42} -> {m['app_repos']} via {m['method']}\")"
