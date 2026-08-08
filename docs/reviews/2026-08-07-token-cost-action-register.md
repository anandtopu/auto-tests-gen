# Review Action Register: Token-cost Accounting

Date: 2026-08-07

| ID | Severity | Status | Owner Area | Finding | Evidence | Recommended Action | Validation Expected | Dependencies |
|---|---|---|---|---|---|---|---|---|
| TCA-R1 | P1 | Completed | Pipeline | A second EXIT trap would strand the run lock | One `_pipeline_exit` trap now flushes then releases | Preserve the implemented handler | Source invariant and requirements draft-stop lock test pass | none |
| TCA-R2 | P1 | Completed | Accounting | Same-label retries can be lost or double-counted | Compatible attempts consolidate with `attempts`; mixed bases become unknown | Preserve non-blending consolidation | Two-attempt and mixed-basis fixtures pass | TCA-A1 |
| TCA-R3 | P1 | Completed | Provider boundary | Parent-level markers would count cache hits | Markers live after cache returns and immediately before real/mock invocation | Preserve exact boundary | Source boundary and never-started tests pass | TCA-A1 |
| TCA-R4 | P2 | Completed | Test isolation | Default-on ledger can write test traffic to the estate | Suite sets `AIQE_COSTS_DIR=out/test-costs` before imports/subprocesses | Preserve import-time redirect | Estate path assertion and full suite pass | TCA-A1 |
| TCA-R5 | P1 | Open | Reporting | Readers see completed run records only | `cost_report.collect()` globs runs | Add sole union accessor and migrate readers | Collision and no-self-resolution pins | TCA-A3 |
| TCA-R6 | P1 | Open | Reconciliation | No provider verification path exists | No adapter usage verb | Add port verb and aligned comparison | Mock arithmetic and conformance | TCA-C2/C3 |

## Status Summary

| Status | Count |
|---|---:|
| Open | 2 |
| In Progress | 0 |
| Blocked | 0 |
| Ready for Verification | 0 |
| Completed | 4 |
| Deferred | 0 |
