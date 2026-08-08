# Review Action Register: TCA-A2 Exit-path Coverage Proof

Date: 2026-08-08

| ID | Severity | Status | Owner Area | Finding | Evidence | Recommended Action | Validation Expected | Dependencies |
|---|---|---|---|---|---|---|---|---|
| TCA-A2-R1 | P1 | Completed | Accounting | Failed mock child was promoted to simulated despite having no result | First eight-path sweep failed mid-kill with `['simulated']` | Propagate child status and require a metered row before simulated classification | Direct regression plus full eight-path rerun | TCA-A1 |
| TCA-A2-R2 | P2 | Completed | Portability | Clarification fixture used GNU-specific `sed -i` | Per-file shell review | Use the existing Python runtime for placeholder replacement | Bash syntax plus exit-65 path | none |
| TCA-A2-R3 | P1 | Completed | Evaluation isolation | A full pipeline sweep could contaminate run metrics and plan approvals | Evaluator state-flow review | Execute against a disposable tracked-file snapshot | Clean source tree except explicit item files; 8/8 sweep | none |
| TCA-R5 | P1 | Open | Reporting | Historical consumers still read completed run records only | Existing TCA review | Implement sole unified accessor | Collision and consumer migration suite | TCA-A3 |
| TCA-R6 | P1 | Open | Reconciliation | Provider billing comparison is absent | Existing TCA review | Add adapter usage port and reconciliation | Conformance and arithmetic suites | TCA-C2/C3 |

## Status Summary

| Status | Count |
|---|---:|
| Open | 2 |
| In Progress | 0 |
| Blocked | 0 |
| Ready for Verification | 0 |
| Completed | 3 |
| Deferred | 0 |
