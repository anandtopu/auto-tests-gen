# Cross-file Integration Checks: Token-cost Accounting

Date: 2026-08-07

## Flow Checks

| Flow | Files Checked | Status | Evidence | Gap/Risk | Action |
|---|---|---|---|---|---|
| Provider call -> live meter -> durable history | `run_phase.sh`, `mock_phase.sh`, `pipeline.sh`, `budget.py`, `run_record.py` | Fail | Metering happens after calls; durability happens only in completed run records | Draft/abort/failure paths lose spend | TCA-A1 start marker and EXIT flush |
| Durable history -> reports/warnings/baselines | `cost_report.py`, `work_queue.py`, `team_report.py`, `parity_compare.py` | Fail | Consumers resolve run records independently | Readers disagree and double-count risk grows | TCA-A3 sole `spend_rows()` accessor and source-resolution pin |
| Cost store -> relocation/backup/reset/retention | `app_paths.py`, `state_bundle.py`, `demo_data.py`, `qa.py`, `.gitignore` | Fail | No cost-store membership | Data loss, estate leak, unbounded growth | Complete A1.4 in the first ledger iteration |
| Key -> statement -> API/UI/export | `qa.py`, `dashboard_server.py`, `dashboard.py`, `Makefile` | Partial | By-key aggregate exists, itemized statement does not | Cannot answer all-attempt task cost | TCA-C1 shared statement model |
| Provider billing -> reconciliation -> alert/badge | adapters, `maintenance.py`, settings/dashboard | Fail | No usage port or reconciliation state | Internal telemetry cannot be externally audited | TCA-C2–C4 through adapter boundary |

## Contract Checks

| Contract | Producer | Consumer | Status | Notes |
|---|---|---|---|---|
| Live TSV completed-call row | `budget.record()` | `run_record.py`, future ledger flush | Pass | Existing columns include provider, basis, tokens, turns, cost |
| Start marker | provider wrappers | future ledger flush | Missing | Must be written only when a call will occur; cache hits are never-started |
| Durable ledger row | future `spend_ledger.py` | future `spend_rows()` | Missing | Schema must keep basis and incomplete values distinct |
| Costs path | `app_paths.costs_dir()` | writer, bundle, reset, prune, tests | Missing | One resolver must own env/state-root precedence |
| Reconciliation usage result | adapter `usage` verb | reconciliation engine | Missing | Unsupported providers return unavailable, never zero |

## Integration Findings

- **TCA-R1 (P1):** installing a second EXIT trap would disable run-lock release.
  Use one handler, flush first, release second, and preserve the incoming status.
- **TCA-R2 (P1):** same-label context retries produce multiple provider charges
  but the planned union key has one phase identity. Aggregate compatible attempts
  and expose the attempt count; never blend incompatible bases.
- **TCA-R3 (P1):** a marker in the parent `PHASE` wrapper would falsely classify
  phase-cache and artifact-cache hits as billed starts. Mark inside the actual
  provider wrappers immediately before invocation.
- **TCA-R4 (P2):** default-on persistence will contaminate the real estate during
  tests unless suite-wide redirection lands in the same commit as the writer.

## TCA-A1 verification update

The provider-call/live-meter/durable-history flow now passes for recorded,
started-unrecorded, and never-started cases. The relocated store is accepted by
state-bundle containment, included in export/import, cleared by demo/factory
reset, pruned with run-record KEEP, and isolated during tests. Historical reader
migration remains intentionally partial until TCA-A3; reconciliation remains
intentionally absent until TCA-C2–C4.
