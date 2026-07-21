#!/usr/bin/env bash
set -euo pipefail
VERB=${1:?verb}; shift || true
case "$VERB" in
  get_item) cat "eval/benchmark/tickets/.item-$1.json" ;;
  search_release)  # tickets whose fix_versions contain $1 (empty arg = all tickets)
    python3 - "$1" << 'PY'
import glob, json, sys
rel = sys.argv[1] if len(sys.argv) > 1 else ""
out = []
for f in glob.glob("eval/benchmark/tickets/.item-*.json"):
    t = json.load(open(f, encoding="utf-8"))
    if not rel or rel in t.get("fix_versions", []):
        out.append({"key": t["key"], "summary": t.get("summary", ""),
                    "fix_versions": t.get("fix_versions", [])})
print(json.dumps(out))
PY
    ;;
  comment)  echo "[mock-jira] $1 <- $2" | tee -a out/mock-comments.log ;;
  *) echo "unknown verb $VERB"; exit 64 ;;
esac
