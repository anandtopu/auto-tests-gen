# TCA-A3 Cross-file Integration Checks

Date: 2026-08-08

## Data flow

`reports/costs` and `reports/runs` converge only in `spend_rows()`. The cost
report then supplies queue envelope warnings, cost baseline/regression,
team/Overview, and dashboard totals. Parity, historical PR comments, and CLI
per-run views call the accessor directly. No consumer imports a vendor SDK.

## Invariants checked

- Live enforcement remains `budget.py` plus `out/cost.tsv`.
- Durable paths remain centralized through `app_paths.costs_dir()`.
- A collision produces one row; a same-label retry retains all attempts without
  adding its aggregate twice.
- `simulated`, `estimated`, `unknown`, `unrecorded`, and `not-reconciled` are not
  blended. Incomplete rows have no fabricated numeric cost.
- Plan/requirements ledger history does not create run records or metrics.
- Parity quality denominators still require run records; abort-only history
  cannot invent gate or critic results.
- Artifact discovery exposes abort-only rows but does not hide completed output.

## Validation

- Syntax compilation: passed for all modified Python production modules.
- Focused/adjacent suite: 16 passed.
- Normalization/regression subset after fixes: 10 passed.
- Full registry compatibility suite after fixes: 1,720 passed in 803.20s.

Residual risk: historical PR cost fallback for a legacy/in-memory record lacking
durable rows remains its persisted aggregate. This preserves compatibility and
does not affect newly durable runs.
