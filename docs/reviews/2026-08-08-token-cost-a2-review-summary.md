# Review Summary: TCA-A2 Exit-path Coverage Proof

Date: 2026-08-08
Reviewer: Codex
Branch/Commit: `codex/test-knowledge-a1-a2` (pre-commit review)

## Overall Status

| Area | Status | Notes |
|---|---|---|
| Per-file pass | Completed | Evaluator, mock controls, pipeline status flow, budget classifier, durable ledger, tests, and Make wiring reviewed |
| Cross-file integration pass | Completed | All five modes and exits 65/77/143 traced from provider boundary through EXIT flush and lock release |
| Tests/build checks | Completed | M1 8/8; 33 focused tests and all 1,716 registry tests passed |
| Release/demo readiness | Ready | TCA-A2 is isolated, deterministic, mock-only, and part of `make eval` |

## Findings

| ID | Severity | Status | Owner Area | Summary | Evidence | Recommended Action |
|---|---|---|---|---|---|---|
| TCA-A2-R1 | P1 | Completed | Accounting | Failed mock call mislabeled simulated | Initial mid-kill sweep failure | Child-status-aware simulation and metered-basis check |
| TCA-A2-R2 | P2 | Completed | Portability | GNU-specific fixture cleanup | Shell per-file review | Python placeholder replacement |
| TCA-A2-R3 | P1 | Completed | Isolation | Eval could pollute operator history | State-flow review | Disposable tracked-file snapshot |
| TCA-R5 | P1 | Open | Reporting | Existing readers omit ledger-only runs | Prior review; outside A2 boundary | Implement TCA-A3 next |

## Completed Scope

- TCA-A2/A2.1 and M1: five modes and three abort paths measured at 100%.
- Mock-only controlled clarification and child-termination fixtures.
- Regression fix for failed-call simulated-basis promotion.
- Standard `make eval` integration and machine-readable result.

## Incomplete Or Deferred Scope

- Unified ledger/run-record history consumption is TCA-A3.
- Task statements, complete consumer report, and reconciliation retain their
  implementation-plan order.

## Validation Evidence

| Check | Result | Notes |
|---|---|---|
| Instrumented exit-path sweep | Pass | 8/8, 100%; exits 0/65/77/143; every lock released |
| Focused accounting/evaluator/budget tests | Pass | 33 passed |
| Ruff on new evaluator/tests | Pass | No findings |
| Python compile / Bash syntax | Pass | Evaluator/tests and changed shell entry points |
| Broad registry suite | Pass | 1,716 passed in 798.13s |

## Next Actions

1. Run staged whitespace checks and commit/push TCA-A2.
2. Advance to TCA-A3 unified spend accessor.
