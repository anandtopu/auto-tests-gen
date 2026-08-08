# TCA-A3 Per-file Analysis

Date: 2026-08-08
Scope: unified historical spend accessor and enumerated consumers

## Findings by file

### `engine/lib/spend_history.py`

- Correctness: guarded reads normalize ledger and run-record rows, reject
  negative/non-finite values, preserve explicit incomplete bases, and dedupe on
  `(run_id, phase)`. Retry aggregates retain ledger quantities and attempts;
  run records contribute enrichment such as `max_turns`.
- Security/reliability: read-only, bounded reporting windows, centralized
  relocatable paths, malformed/torn files skipped independently.
- Finding fixed: run-record `turns_used` initially did not map to canonical
  `turns`; the full suite exposed and the final implementation corrects it.

### `engine/lib/cost_report.py`

- Correctness: the accessor supplies all spend; record reads remain only for
  non-spend artifact-reuse metadata. Call counts use `attempts`, while aggregate
  dollars/tokens are added once. Unknown, unrecorded, and not-reconciled calls
  make the total visibly incomplete.
- Finding fixed: malformed spend-bearing records were initially admitted as
  metadata-only runs. They are now admitted only through accessor validation;
  genuine no-spend records remain available for non-spend metrics.
- Maintainability: removed the obsolete local spend normalizer.

### `engine/lib/parity_compare.py`

- Spend/provider/basis/turn data comes from the union. Gate and critic metrics
  remain run-record-owned, preserving A1.3. Simulated exclusion is unchanged.

### `engine/lib/pr_comment.py`

- Live comments still use `budget.total(out/cost.tsv)`. Historical replay uses
  the union and falls back to the supplied aggregate for in-memory/legacy rows.

### `bin/qa.py`

- `status --cost` and artifact spend tables use union rows. Ledger-only aborts
  are discoverable; default artifact output still prefers a rich run record.
  Incomplete counts render as `-` and incomplete bases as labels.

### `registry/tests/test_spend_history.py`

- Pins collision dedupe, retry preservation, abort-only inclusion, incomplete
  reporting, and the no-direct-source consumer boundary.

No open P0-P2 per-file defect remains.
