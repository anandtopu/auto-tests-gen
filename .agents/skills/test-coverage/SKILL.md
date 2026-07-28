---
name: test-coverage
description: Answer "what is and isn't covered" from the test catalog and harvested
  surface, and turn the gaps into concrete generation targets — evidence-based, never
  hand-edited coverage.
triggers: [test coverage, coverage gaps, improve coverage, coverage report]
metadata:
  version: "1.1"
  bundles: scripts/coverage-snapshot.sh
---
# Test coverage (AI-QE agent)

Coverage here is EVIDENCE: every cataloged test maps to app repos with confidence,
`covers` in the registry is generated (catalog evidence ∪ declared scope), and gaps
are computed by diffing the harvested API/route surface against that evidence.

## Steps

0. One bundled command gives you the whole picture:

   ```bash
   bash ./scripts/coverage-snapshot.sh
   ```

   (matrix + ranked gaps + rotting coverage). The individual commands, if you
   need them separately:

1. `make coverage` — the app-repo × test-repo matrix with gap warnings.
2. `make gaps` — harvested surface (endpoints, routes) with NO test evidence; the
   same data feeds every generation phase as `out/coverage-gaps.md`, and AGENTS.md
   marks uncovered surface `[NO TEST]`.
3. Drill in with the catalog index:
   `python3 bin/qa.py sql "SELECT app_repo, COUNT(*) FROM mappings GROUP BY app_repo"`
   `python3 bin/qa.py sql "SELECT title, pass_rate FROM tests WHERE pass_rate < 1"`
   (CI health from `make ingest-results` shows which existing coverage is rotting.)
4. Report: ranked gaps (risk-weighted — mutating endpoints and authz paths first),
   which test repo each gap routes to, and whether an existing spec is extendable.
5. To CLOSE a gap, hand it to generation — `bash engine/pipeline.sh jira <KEY>` for
   a story that covers it, or `python3 bin/qa.py run-inline "<gap as a story>"
   --repos <app_repo>` when no ticket exists. Never write specs directly.

## Constraints (non-negotiable)

- **Never hand-edit `covers:`**, the catalog JSONL, or AGENTS.md — they are all
  generated; mapping corrections go through `python3 bin/qa.py map / apply-review`.
- Scope changes (which app repos a test repo is responsible for) go through
  `bin/repos.py scope` — that is a human decision; propose, don't apply.
