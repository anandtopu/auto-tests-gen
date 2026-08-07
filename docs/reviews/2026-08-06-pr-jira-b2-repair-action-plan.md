# B2 Bounded Repair — Review Action Plan

Date: 2026-08-06
Status: Complete

No open P0–P2 B2 finding remains. The focused, related, and full registry
matrices pass after the review fixes.

| Action | Priority | Acceptance | Status |
| --- | --- | --- | --- |
| Bind repair claims to physical edits. | P1 | Changed source paths exactly equal fix/test evidence; omitted and fictitious edits fail. | Done |
| Revalidate repair contracts at apply and durable-read boundaries. | P1 | Tampering between phases or in a historical run cannot be surfaced as valid. | Done |
| Deny write-enabled repair from both cache layers. | P1 | `reviewrepair` lookup/store/restore always refuses. | Done |
| Preserve unresolved findings through no-op/repeated/approve paths. | P1 | Surface verdict remains needs-work until the finding is genuinely addressed and absent on rereview. | Done |
| Use the shared Windows-safe atomic writer. | P2 | No durable writer calls bare `os.replace`; regression passes. | Done |
| Pin phase policy, metering labels, shell order, and mock execution. | P2 | Phase inventory, provider capability, syntax, and end-to-end loop tests pass. | Done |

## Follow-on

- B3: implement org-config-only `off|warn|require` delivery policy, unavailable
  handling, pre-gate refusal, and constitution pins.
- Operational: measure the real B6 reviewer and B2 repair behavior only after
  parity authentication is available; do not treat mock outcomes as judgement
  quality or production cost evidence.
