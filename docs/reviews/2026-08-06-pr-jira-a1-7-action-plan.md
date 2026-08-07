# A1.7 TaskEvent Explicit PR Ticket — Review Action Plan

## Release Gate

Ready to commit and push. No open P0, P1, or P2 action remains.

## Fix Queue

| ID | Priority | Owner | Action | Acceptance check |
|---|---|---|---|---|
| A1.7-R1 | P2 | Platform | Enforce the schema's string key type at the receiver boundary. | Non-string input returns HTTP 400 and leaves queue/seen state unchanged. Completed. |
| A1.7-R2 | P3 | Platform | Clean modified-file import lint. | Changed-file Ruff passes. Completed. |

## Follow-Up Backlog

Next eligible item: A4, discovery evaluation with labelled signal/conflict
fixtures and explicit precision, recall, and correct-refusal metrics.
