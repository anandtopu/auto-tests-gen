# Per-file Analysis: Token-cost Accounting PRD and Current Implementation

Date: 2026-08-07

| File | Responsibility | Local Status | Findings | Test/Validation Gap | Action |
|---|---|---|---|---|---|
| `docs/prd-token-cost-accounting.md` | Product contract and delivery slices | Issue | Complete exit-path and basis requirements; same-label retry consolidation is not specified although retries write multiple live TSV rows | No acceptance case for two calls sharing a phase label | Record the risk and pin compatible aggregation in TCA-A1/TCA-A3 |
| `engine/pipeline.sh` | Run lock, budget guard, phase orchestration, terminal paths | Issue | Only EXIT trap releases the lock; plan, requirements, 65, 77, and some failure exits precede durable run-record writes | No durable-spend assertion across terminal paths | Replace with one chained flush-then-release handler; preserve `$?` |
| `engine/phases/run_phase.sh` | Provider resolution and invocation | Issue | It is the first layer that knows a provider call will really happen, but records no start fact | Mid-call death reads as zero/no row | Mark immediately before adapter `run_phase` |
| `engine/phases/mock_phase.sh` | Deterministic provider simulation | Issue | Mock sweeps cannot prove started-vs-never-started semantics | No controlled unrecorded fixture | Emit the same start marker before simulated work |
| `engine/lib/budget.py` | Live per-run metering and enforcement | OK | TSV already carries the needed completed-call data; it must remain live-run-only | No durable flush consumer | Reuse without changing enforcement semantics |
| `engine/lib/run_record.py` | Completed-run monitoring record | Issue | Plan/requirements/abort paths intentionally or accidentally omit it; same-label rows overwrite in `spend_by_phase` | No pin separating run metrics from spend history | Keep invariant; later consume ledger only through TCA-A3 |
| `engine/lib/cost_report.py` | Historical aggregation | Issue | Reads only run records | Plan/aborted spend and unrecorded calls invisible | Migrate through unified accessor in TCA-A3 |
| `engine/lib/app_paths.py` | Mutable path precedence and relocation | Issue | No costs-directory resolver | `AIQE_COSTS_DIR`/`AIQE_STATE_DIR` cannot be consistently honored | Add `costs_dir()` and resolver mapping |
| `engine/lib/state_bundle.py` | Portable durable-state export/import | Issue | Cost history is not included | Migration loses spend evidence | Include relocated `reports/costs` in full profile |
| `engine/lib/demo_data.py` | Clear-demo/factory reset | Issue | Cost history is not cleared | Reset leaves simulated/user cost entries | Add relocated costs directory |
| `bin/qa.py` | Retention operations | Issue | Prune only removes run records/diffs and artifacts | Ledger grows without bound | Prune costs with the same KEEP policy |
| `registry/tests/conftest.py` | Suite-wide state isolation | Issue | No cost-ledger redirection | Pipeline tests would write to the real estate once default-on ships | Set `AIQE_COSTS_DIR` before imports/subprocesses and pin it |
| `.gitignore` | Runtime state hygiene | Issue | No name-shaped cost-entry rule | Every run would dirty the checkout or a blanket rule could hide future named state | Ignore `reports/costs/[0-9]*.json` only |

## Notes

- The live `AIQE_COST_LEDGER` name and semantics remain unchanged; the durable
  store uses `AIQE_SPEND_LEDGER` and `AIQE_COSTS_DIR` exactly as the PRD requires.
- No reconciliation implementation may import a vendor in engine code; that is
  reserved for the adapter `usage` verb in TCA-C2.

## TCA-A1 implementation outcome

- The durable writer and exact provider-boundary markers close the confirmed
  exit-path hole without changing run-record/commit-rate semantics.
- Cross-file review found and fixed two actionable defects: relocated bundle
  imports initially lacked the costs-store containment anchor, and empty live
  rows from pre-provider failures initially risked being called `unrecorded`.
- Post-review accounting/lifecycle suite: 88 passed. Expanded compatibility suite:
  121 passed. Full registry suite: 1,710 passed in 767.61s. Ruff on new Python,
  Python compilation, Bash syntax, and whitespace checks passed.
- No unresolved P0/P1/P2 finding remains in TCA-A1. TCA-R5/R6 are planned later
  backlog boundaries, not defects in the completed ledger slice.
