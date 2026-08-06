# Review Action Register: A2 PR + JIRA Context Fusion Plan

Date: 2026-08-06

| ID | Severity | Status | Owner Area | Finding | Evidence | Recommended Action | Validation Expected | Dependencies |
|---|---|---|---|---|---|---|---|---|
| A2-F1 | P1 | Open | Discovery/Tracker | Successful response identity is unchecked. | `pipeline.sh:335` | Require object JSON with exact requested key before `valid`. | wrong-key/malformed/empty success tests | A1 |
| A2-F2 | P1 | Open | Pipeline | Shared consumers lack canonical PR `ticket.json`. | `pipeline.sh:356,391` | Atomically promote selected checked bytes; no refetch. | one-call and byte-equality tests | A2-F1 |
| A2-F3 | P1 | Open | Context | ACs are not budgeted/rendered. | `context_scope.py:120,167` | Framed manifest tail; AC mandatory, prose optional. | 1-token/determinism/omission tests | A2-F2 |
| A2-F4 | P2 | Open | Guidance | Policy is branch-local shell. | `pipeline.sh:408-413` | Emit guidance kind from one ticket parse for both paths. | PR/JIRA precedence parity | A2-F2 |
| A2-F5 | P2 | Open | Prompt/security | Raw fusion lacks bounded framing and tail evidence. | `run_phase.sh:93-104` | Pure bounded Markdown renderer appended last. | hostile-text/order/cache-key tests | A2-F3 |
| A2-F6 | P2 | Completed | Product/QA | A2.5 baseline ambiguous. | PRD A1.5/A2.5 | Use `c9a4a3f` as baseline and retain A1 no-ticket tail. | golden flag/state matrix | none |
| A2-F7 | P2 | Open | Reliability | Stale canonical/guidance files could survive a no-selection retry. | scratch file lifecycle | Clear/overwrite before fusion and assert absence per outcome. | sequential stale-artifact adversarial test | A2-F2 |

## Status Summary

| Status | Count |
|---|---:|
| Open | 6 |
| In Progress | 0 |
| Blocked | 0 |
| Ready for Verification | 0 |
| Completed | 1 |
| Deferred | 0 |
