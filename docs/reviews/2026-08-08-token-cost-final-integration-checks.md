# Token-cost final integration checks

## Scope

Cross-file trace from provider call start through live enforcement, durable
history, reports/statements, reconciliation, maintenance, UI, and state lifecycle.

## Findings

| ID | Severity | Boundary | Evidence | Result |
| --- | --- | --- | --- | --- |
| F-01 | — | Provider call → live budget → durable ledger | start/completion/exit sweep; live ledger remains `budget.py` authority | Pass |
| F-02 | — | Durable ledger + run record → history union | collision and retry fixtures | Pass; M3 = 0 |
| F-03 | — | Union → every historical consumer | no-self-resolution source pin and complete registry suite | Pass |
| F-04 | — | Task/probe/embedding → report | complete consumer fixture and runtime report | Pass; M2 complete |
| F-05 | — | Exact key → statement/API/artifacts | partitioned line items and export/API tests | Pass |
| F-06 | — | Usage adapter → reconciliation → Notify/UI | conformance, mock smoke, unavailable/timeout tests | Pass |
| F-07 | — | Maintenance → deployment outcome | exit-75-only degradation and CronJob tests | Pass |
| F-08 | — | Mutable cost state → relocation/bundle/clear/prune | lifecycle and adversarial tests | Pass |
| F-09 | — | Five modes + abort/failure paths | isolated evaluator | Pass; M1 = 8/8 |
| F-10 | — | Secrets/vendor boundary | credential only in settings/examples/adapter; engine port pin | Pass |

## Validation

The complete registry suite passed 1,767/1,767 in 13m44s. `make cost-report`,
`qa.py status --cost`, `qa.py cost-statement PROJ-301`, and isolated mock
`make cost-reconcile` returned zero and preserved explicit simulated/basis state.

## Open Questions

Real-provider M4 cannot be numerically evaluated in this environment. The PRD
explicitly excludes that external authorization from its code delivery gate.
