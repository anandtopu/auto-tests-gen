# A1.6 Terminal Ticket Status — Review Action Plan

## Release Gate

Ready to commit and push. No open P0, P1, or P2 action remains.

## Fix Queue

| ID | Priority | Owner | Action | Acceptance check |
|---|---|---|---|---|
| A1.6-R1 | P2 | Platform | Revalidate recorded status before rendering a terminal warning. | Forged stored boolean produces no warning; valid Closed/Done still warns. Completed. |
| A1.6-R2 | P3 | Platform | Clean modified-file import lint. | Ruff passes. Completed. |

## Follow-Up Backlog

Next eligible item: A1.7, optional TaskEvent PR-key propagation while preserving
the pre-key replay-dedupe identity. A4 follows after A1.7.
