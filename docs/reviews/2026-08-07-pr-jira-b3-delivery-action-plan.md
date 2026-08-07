# PR/JIRA B3 Delivery Policy — Review Action Plan

Date: 2026-08-07
Status: Complete

| ID | Priority | Action | Evidence/status |
| --- | --- | --- | --- |
| B3-R1 | P2 | Make refusal evidence run-scoped. | Explicit current-run path plus record integration test. **Done.** |
| B3-R2 | P2 | Keep review refusal distinct from no-changes/quarantine. | CLI, dashboard, wizard, team report, scorecard, and tests updated. **Done.** |
| B3-R3 | P2 | Bound comment/shell refusal text. | Newlines collapsed; strict list/text ceilings retained. **Done.** |
| B3-R4 | P2 | Preserve default-off artifact parity. | Delivery called only for current reviewer evidence; require forces review. **Done.** |
| B3-R5 | P3 | Reconcile post-commit learning order pin. | Early and normal run-record calls are separately ordered. **Done.** |

No open P0–P2 action remains. Residual product risk is reviewer quality under real-model estate traffic: policy therefore defaults to measured `warn`, B6 clean/seeded evidence remains visible, and teams should select `require` only after acceptable false-refusal evidence. B5 is the next eligible backlog item.
