#!/usr/bin/env bash
# Coverage snapshot — matrix + ranked gaps + CI health in one read-only pass.
# Bundled with the skill so the agent never has to improvise the query set.
set -uo pipefail
cd "${AIQE_ROOT:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../../../.." && pwd)}"

echo "== Coverage matrix (app repo x test repo) =="
python3 bin/qa.py coverage 2>/dev/null || echo "(registry empty)"

echo
echo "== Gaps: harvested surface with NO test evidence =="
python3 bin/qa.py gaps 2>/dev/null | head -40 || true

echo
echo "== Rotting coverage (CI health: anything below a clean pass rate) =="
python3 bin/qa.py sql \
  "SELECT title, pass_rate FROM tests WHERE pass_rate IS NOT NULL AND pass_rate < 1" \
  2>/dev/null || echo "(no CI health ingested — make ingest-results FILE=junit.xml)"
