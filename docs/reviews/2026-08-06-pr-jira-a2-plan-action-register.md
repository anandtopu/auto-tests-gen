# Review Action Register: A2 PR + JIRA Context Fusion Plan

Date: 2026-08-06

| ID | Severity | Status | Owner Area | Finding | Evidence | Recommended Action | Validation Expected | Dependencies |
|---|---|---|---|---|---|---|---|---|
| A2-F1 | P1 | Completed | Discovery/Tracker | Successful response identity is unchecked. | `ticket_discovery.py::validate_ticket_response` | Exact key, object shape, and bounded response validation implemented. | wrong-key/malformed/array/AC-bound tests pass | A1 |
| A2-F2 | P1 | Completed | Pipeline | Shared consumers lack canonical PR `ticket.json`. | selected-candidate promotion in `pipeline.sh` | Selected checked bytes are copied atomically with no refetch. | functional one-call/canonical tests pass | A2-F1 |
| A2-F3 | P1 | Completed | Context | ACs are not budgeted/rendered. | `ticket_context.py` | Framed manifest tail retains every validated AC and budgets prose. | one-token/determinism/omission tests pass | A2-F2 |
| A2-F4 | P2 | Completed | Guidance | Policy is branch-local shell. | `ticket_fields.py::fields` | One classifier preserves label/bug/security/story precedence for both paths. | precedence and parity tests pass | A2-F2 |
| A2-F5 | P2 | Completed | Prompt/security | Raw fusion lacks bounded framing and tail evidence. | `ticket_context.py`, phase-tail wiring | Pure bounded Markdown is framed as untrusted data and appended last. | hostile-text/order/cache-key tests pass | A2-F3 |
| A2-F6 | P2 | Completed | Product/QA | A2.5 baseline ambiguous. | PRD A1.5/A2.5 | Use `c9a4a3f` as baseline and retain A1 no-ticket tail. | golden flag/state matrix | none |
| A2-F7 | P2 | Completed | Reliability | Stale canonical/guidance files could survive a no-selection retry. | PR scratch cleanup before flag evaluation | Canonical, guidance, and fusion files are cleared before every PR run. | sequential flag-off stale-artifact test passes | A2-F2 |

## Status Summary

| Status | Count |
|---|---:|
| Open | 0 |
| In Progress | 0 |
| Blocked | 0 |
| Ready for Verification | 0 |
| Completed | 7 |
| Deferred | 0 |
