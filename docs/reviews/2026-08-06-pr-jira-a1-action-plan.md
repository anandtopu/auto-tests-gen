# PR + JIRA A1 Ticket Discovery — Review Action Plan

Date: 2026-08-06

## Release gate

Targeted policy tests, queue/settings compatibility tests, adapter conformance,
shell syntax, broad registry tests, cached whitespace checks, exact-file
staging, commit, push, and upstream parity are required before A1 is complete.

## Fix queue

| ID | Priority | Owner | Action | Status | Acceptance check |
| --- | --- | --- | --- | --- | --- |
| A1-R1 | P1 | A1 | Separate Tracker not-found from unavailable. | Completed | Missing mock/real issue returns 3; transport/non-200 remains unavailable. |
| A1-R2 | P2 | A1 | Preserve legacy queue JSON shape. | Completed | Queue items without explicit ticket contain no `ticket` field. |
| A1-R3 | P2 | A1 | Close and bound metadata states/fields. | Completed | Unexpected states normalize to unavailable; input limits are enforced. |
| A1-R4 | P2 | A1 | Make earned parser import-safe and reuse it. | Completed | Import does not consume caller argv; false-positive grammar tests pass. |
| A1-R5 | P2 | A1 | Preserve recording/replay curl compatibility while adding HTTP classification. | Completed | Existing ADF/comment tests and new 404/503 status tests pass together. |
| A1-I1 | P1 | A1 | Validate every candidate and refuse ambiguity. | Completed | Invalid, unavailable, multi-key, explicit, and branch-priority tests pass. |
| A1-I2 | P2 | A1 | Preserve flag-off execution. | Completed | Discovery block and phase argument are absent when the flag is off. |
| A1-I3 | P2 | A1 | Scope explicit key to one queue item. | Completed | Runner environment propagation and dedupe tests pass. |
| A1-I4 | P2 | A1 | Persist explainable provenance. | Completed | Historical run-record explain test identifies signals, validation, and rule. |

## Follow-up backlog

| Item | Owner | State |
| --- | --- | --- |
| Materialize and fuse the validated ticket using existing ticket machinery. | A2 | Next eligible |
| Measure provider-specific signal precision, recall, and commit pagination needs. | A4 / E1 | Planned |
| Decide whether all discovered links should comment on the ticket. | Product / E5 | Open question |

No open A1 code action remains. Focused checks, adapter conformance, shell
syntax, and all 1,423 registry tests pass. Git delivery is the remaining gate.
