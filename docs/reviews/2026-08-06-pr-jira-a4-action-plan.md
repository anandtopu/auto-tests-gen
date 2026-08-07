# A4 Discovery Evaluation — Review Action Plan

## Release Gate

Ready to commit and push. No open P0, P1, or P2 action remains.

## Fix Queue

| ID | Priority | Owner | Action | Acceptance check |
|---|---|---|---|---|
| A4-R1 | P2 | QE Platform | Preserve fixture identity in final-decision metric samples. | Two fixtures sharing a ticket count as two true positives. Completed. |
| A4-R2 | P2 | QE Platform | Make the PRD's 95% precision floor non-lowerable by fixture labels. | A lower label declaration fails before evaluation. Completed. |
| A4-R3 | P2 | QE Platform | Document the new standalone evaluator target. | Documentation currency test passes. Completed. |
| A4-R4 | P3 | QE Platform | Clean modified-file scorecard lint. | Changed-file Ruff passes. Completed. |

## Follow-Up Backlog

Next eligible item: B1, the read-only test reviewer phase after validate and
before the deterministic gate.
