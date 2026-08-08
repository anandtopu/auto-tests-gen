# Cross-file Integration Checks: TCA-A2 Exit-path Coverage Proof

Date: 2026-08-08

## Flow Checks

| Flow | Files Checked | Status | Evidence | Gap/Risk | Action |
|---|---|---|---|---|---|
| Five supported modes -> provider calls -> durable ledger | `token_cost_coverage.py`, `pipeline.sh`, `spend_ledger.py` | Pass | Requirements, plan, tests, jira, and pr each produced exactly one entry | None | Keep evaluator in `make eval` |
| Clarification 65 -> flush -> unlock | `mock_phase.sh`, `spec_store.py`, `pipeline.sh` | Pass | Exit 65 retained recorded analyze only; lock absent | None | None |
| Budget 77 -> completed row, no guarded row -> unlock | `budget.py`, `pipeline.sh`, `spend_ledger.py` | Pass | Exit 77 retained simulated analyze; testplan absent | False unrecorded guarded row would overstate billing | Adversarial assertion pins absence |
| Child TERM -> start marker -> unknown durable row -> unlock | `mock_phase.sh`, `pipeline.sh`, `budget.py`, `spend_ledger.py` | Pass | Exit 143 retained one unrecorded analyze row | First run mislabeled it simulated | Child status now suppresses fallback simulation |
| Eval -> mutable state | `token_cost_coverage.py`, `app_paths.py` | Pass | All runs execute in a temporary tracked-file snapshot | Accidental scorecard/plan pollution | Sandbox is deleted after result extraction |

## Contract Checks

| Contract | Producer | Consumer | Status | Notes |
|---|---|---|---|---|
| Child phase status | `pipeline.sh` | `budget.py record` | Pass | Zero permits mock simulation; nonzero still harvests any real result but cannot invent simulation |
| Live metered flag/basis | `budget.py` | `spend_ledger.py` | Pass | Blank, unmetered fallback is unrecorded when a start marker exists |
| Start-marker boundary | `mock_phase.sh` | `spend_ledger.py` | Pass | Controlled TERM is after mark-start and before a result |
| M1 result | `token_cost_coverage.py` | `make eval` / reviewers | Pass | 8 eligible invocations, 8 durable entries, 100% |

## Integration Findings

- **TCA-A2-R1 (P1, fixed):** a failed mock child was charged as simulated because
  mock mode, rather than a metered success, selected the basis.
- **TCA-A2-R2 (P2, fixed):** the first clarification fixture cleanup used
  platform-specific `sed -i`; the final path uses Python consistently.
- No production adapter imports or provider behavior changed.
