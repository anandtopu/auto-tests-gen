# TCA-C4 integration checks

| Boundary | Evidence | Result |
|---|---|---|
| Provider usage port → reconciliation | C3 arithmetic remains the sole comparison path; no vendor import | Pass |
| Comparison → threshold | exact Decimal, strict “beyond”, zero-denominator disagreement forced to drift | Pass |
| Drift → Notify port | message names both figures, UTC window, missed harvests and shared-key workloads | Pass |
| Notify failure → operations | persisted `reconciled-drift` plus failed delivery; exit 75/DEGRADED | Pass |
| Credential/API/timeout → status | explicit `not-reconciled`, never a zero or healthy comparison | Pass after C4-01 |
| State → dashboard API/UI | atomic cost-state file; fail-closed loader; exactly three badge labels | Pass |
| Reconciliation → maintenance | nightly step; external exit 75 degraded, local exit 1 failed | Pass after C4-02 |
| State → lifecycle | `AIQE_COSTS_DIR`, state bundle, demo clear, history skip, prune skip, exact gitignore | Pass |
| Reconciliation → ledger/budget | read-only result and alarm; live budget TSV and durable rows unchanged | Pass |
| Security | write-only Admin key stays in adapter/settings; persisted document and alarm contain no credential | Pass |

Validation includes 32 focused tests, the isolated mock Make/Notify journey,
the isolated missing-credential exit-75 journey, Ruff on the reconciliation
module/tests, Python compilation of every changed Python file, and 267 broad
cost/state/settings/maintenance/adapter/API compatibility tests. A first
focused API group had one adjacent cost-statement timeout; the exact pair passed
2/2 immediately on rerun, so no reproducible product failure remains.
