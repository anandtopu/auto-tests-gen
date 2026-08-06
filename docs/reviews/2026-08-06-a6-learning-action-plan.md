# A6 Learning Loop — Action Plan

Date: 2026-08-06

## Release gate

Focused and broad tests, targeted lint, cached whitespace check, exact-file
staging, commit, push, and upstream parity are required before A6 is complete.

## Fix queue

| ID | Priority | Action | Status | Acceptance check |
| --- | --- | --- | --- | --- |
| A6-R1 | P1 | Refuse torn provenance overwrite. | Completed | Damaged bytes remain unchanged; ranking reports unavailable. |
| A6-R2 | P2 | Persist full verified commit SHAs. | Completed | Short gate SHA resolves to the full commit in event/result. |
| A6-R3 | P2 | Use latest review rather than cumulative transition count. | Completed | changes-requested → approval → repeated approval yields exactly +1. |
| A6-R4 | P2 | Surface outcome-ranking availability. | Completed | Impact artifact states disabled/measured/unavailable and applied. |

## Follow-up backlog

| Item | Owner | State |
| --- | --- | --- |
| Capture reviewer edit diffs from CI and relate them to produced chunks. | PRD D6 / future observed tier | Out of A6 scope |

No open A6 code action remains; broad compatibility validation and Git delivery
are the remaining iteration gates.

Validation complete: 85 focused tests and all 1,349 registry tests passed;
targeted lint and pipeline shell syntax are clean. Cached diff checks, commit,
push, and upstream parity remain the final delivery gates.
