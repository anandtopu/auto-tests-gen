# TCA-C2 action register

| ID | Priority | Finding | Resolution | Status |
|---|---:|---|---|---|
| C2-01 | P1 | Provider amounts are fractional cents; float parsing or treating them as dollars misstates spend. | Adapter sums `Decimal` values and divides by 100; regression proves `$1.24`. | Fixed |
| C2-02 | P1 | Unsupported/unconfigured providers could be mistaken for a legitimate zero bill. | `unavailable` is mandatory and cannot contain `cost`/`cost_usd`; engine and conformance reject violations. | Fixed |
| C2-03 | P2 | Unbounded or repeated provider cursors could loop forever or double-count. | Maximum 100 pages and repeated-cursor refusal; pagination behavior is tested. | Fixed |
| C2-04 | P2 | Initial implementation omitted the new secret from the OpenShift example. | Added `ANTHROPIC_ADMIN_KEY` to the deployment secret example and integration inventory. | Fixed |
| C2-05 | P2 | Broad verification rewrote tracked PROJ-301 plan artifacts. | Redirected every authored plan/spec/testdata path in the standalone full-run test; before/after hashes now remain equal. | Fixed |
| C2-07 | P1 | The initial engine contract accepted negative, non-finite, non-decimal amounts and empty/non-UTC windows from an adapter. | Validate exact Decimal amount and increasing UTC timestamps before exposing provider evidence; adversarial cases added. | Fixed |
| C2-06 | P3 | Full registry sweep exceeded the 600-second command timeout. | Recorded as timed out, not passed; 343 dependency-focused compatibility tests and 165 focused tests passed. Runtime optimization remains outside C2. | Residual |

Next product work is TCA-C3 provider-aligned ledger comparison. C2 intentionally
does not persist or interpret reconciliation state.
