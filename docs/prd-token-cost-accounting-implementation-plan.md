# Token-cost Accounting — Implementation Plan

Date: 2026-08-07
Source: [prd-token-cost-accounting.md](prd-token-cost-accounting.md) (Draft v2)

## Delivery order and status

| Order | Item | PRD mapping | Dependencies | Status | Implementation boundary |
| ---: | --- | --- | --- | --- | --- |
| 1 | TCA-A1 Durable spend ledger | A1.1, A1.1a, A1.3–A1.5a, A1.7 | none | Implemented | Default-on durable per-run ledger, chained EXIT flush, exact start markers, state lifecycle, attribution, no run-record regression |
| 2 | TCA-A2 Exit-path coverage proof | A2.1, M1 | TCA-A1 | Implemented | Isolated instrumented sweep of five modes plus clarification, budget-abort, and mid-call failure paths; M1 measured at 8/8 |
| 3 | TCA-A3 Unified spend accessor | A1.2, A1.2a | TCA-A1 | Implemented | One deduplicating `spend_rows()` authority; run-record enrichment wins; every enumerated consumer migrates or remains explicitly live-run-only |
| 4 | TCA-C1 Per-task cost statement | C1.1–C1.3, G5 | TCA-A3 | Implemented | CLI, API, artifacts-adjacent panel, per-basis totals, incomplete-state counts, CSV/Markdown exports |
| 5 | TCA-B1 Complete consumer report | B1.1–B1.4, M2 | TCA-A3, TCA-C1 | Planned | Embedding section, probe attribution separation, unmeterable line, shared provider/basis rollups |
| 6 | TCA-C2 Provider usage port | C2.1, C2.1a | TCA-A3 | Planned | Adapter-family `usage <window>` verb, mock fixture, conformance suite, write-only admin credential, Make entry point |
| 7 | TCA-C3 Reconciliation arithmetic | C2.1b, C2.2 | TCA-C2 | Planned | Provider-aligned UTC windows, reported-only comparison, reconcilable fraction, deterministic drift evidence |
| 8 | TCA-C4 Reconciliation operations | C2.3, C2.4, M4 | TCA-C3 | Planned | Notify alarm, three-state Cost badge, DEGRADED maintenance step, no-credential/API-down honesty |
| 9 | TCA-FINAL Broad verification | M1–M4 and guardrails | TCA-A2–TCA-C4 | Planned | Full compatibility suite, report/runtime regression checks, final docs/status reconciliation |

The sequence follows the PRD delivery slices. TCA-A3 is intentionally after the
exit-path proof: consumers should not migrate to a new history source until that
source has demonstrated complete durable capture.

## Acceptance mapping

### TCA-A1 — Durable spend ledger

| Criterion | Implementation | Verification |
| --- | --- | --- |
| A1.1 | `spend_ledger.py` converts the live `AIQE_COST_LEDGER` TSV into `reports/costs/<RUN_ID>.json`; every row carries run/mode/key/phase/provider/model/basis/tokens/turns/cost/timestamp/attribution | Unit schema, malformed-input, disabled-knob, path-isolation tests; pipeline plan-mode test |
| A1.1a | One `_pipeline_exit` handler receives `$?`, flushes best-effort, emits a failure event when possible, releases the lock second, and returns the original status | Source invariant plus successful and exit-77 functional lock-release tests |
| A1.3 | Plan and requirements draft stops gain ledger entries but still never write run records | Functional mode tests and run-record absence assertions |
| A1.4 | `app_paths.costs_dir()` owns relocation; writes use `fs_lock`; costs join state bundle, name-shaped gitignore, clear-demo/factory, prune, and pytest estate isolation | Relocation, bundle, clear, prune, gitignore, unlocked-write and estate-leak pins |
| A1.5/A1.5a | Real adapter and mock provider wrappers write a start marker immediately before a provider call; flush emits `unrecorded` only for a marker without a completed row | never-started / started-unrecorded / recorded unit tests and mid-call failure sweep |
| A1.7 | `AIQE_COST_ATTRIBUTION` is copied to every row, defaulting to `user`; non-user callers set an explicit bounded stamp | Default/custom/malformed attribution tests; cache-probe integration in TCA-B1 |

Same-label context retries are consolidated within the ledger before the
cross-source union. Token counts and costs are summed only when their bases are
compatible; an incompatible basis mix becomes incomplete rather than a blended
dollar claim. The row records `attempts` so consolidation remains auditable.

### TCA-A2 — Exit-path coverage proof

Drive `pr`, `jira`, `plan`, `tests`, and `requirements` in the mock estate.
Add controlled clarification (65), budget abort (77), and provider child failure
cases. Assert one durable entry for every invocation that actually starts a call,
no row for a guard-stopped never-started phase, and no stale pipeline lock.

Implementation evidence: `eval/token_cost_coverage.py` snapshots the tracked
working tree into a disposable estate, drives all eight enumerated scenarios
through `engine/pipeline.sh`, and writes its ignored machine-readable result to
`eval/results/token-cost-coverage.json`. The measured result is M1 **8/8
(100%)**. Exit 77 records only the completed `analyze` call, while a terminated
provider child records one `unrecorded` `analyze` row; every scenario releases
`out/.pipeline.lock`. The sweep exposed and fixed an empty failed-mock row that
had been mislabeled `simulated`. Focused accounting/evaluator checks passed (33)
and the full registry compatibility suite passed (1,716).

### TCA-A3 — Unified spend accessor

Create the sole history accessor over ledger entries and run-record spend blocks.
Deduplicate source collisions by run and phase, with the enriched run-record row
winning. Move cost report, queue warning, baseline/regression, team/overview,
parity, PR comments, and artifact views to it as specified. Add a repository pin
that rejects production modules resolving either history source directly.

Implementation evidence: `engine/lib/spend_history.py` is the read-only union
authority and normalizes both durable sources into explicit basis-aware rows.
Collisions use `(run_id, phase)`; enriched run-record fields win while the
ledger's compatible quantitative aggregate and `attempts` survive retries.
`cost_report` now feeds queue warnings, baselines/regression, team/overview and
dashboard totals from the union. Parity, historical PR comments, `qa status
--cost`, and artifacts use the same accessor; abort-only histories are visible
without displacing the newest rich artifact record. The live `budget.py` path
is unchanged. Focused/adjacent verification passed (16), the normalization
regressions passed (10), and the full registry suite passed (1,720).

### TCA-C1 — Per-task cost statement

Build a pure statement model first, then expose it through `make cost-statement`,
`GET /api/cost-statement`, and the key artifacts view. Totals remain partitioned
by basis and count `unknown`/`unrecorded`. CSV and Markdown exports use the
existing `reports/exports` location and one line per phase.

Implementation evidence: `cost_statement.py` selects exact keys from the TCA-A3
union, lists every run/phase with attempts, and partitions user totals into
reported, estimated, simulated, local-token, unknown, unrecorded,
not-reconciled, and structurally incomplete states. Non-user attribution is
listed and exported outside task totals. `make cost-statement`, the QA CLI,
authenticated JSON/Markdown/CSV API, and artifact-panel downloads share the
same model. Ledger-only plan/requirements/abort keys appear in Artifacts without
entering run metrics. Exports use a locked atomic replace under relocatable
`AIQE_EXPORTS_DIR`. Targeted/adjacent checks passed (151), final focused review
checks passed (15 and 26), and the full registry suite passed (1,734).

### TCA-B1 — Complete consumer report

Normalize daily embedding rows into the report without moving its cap. Mark
cache-probe pipeline invocations as `probe` and exclude them from user task
totals. Count unknown/OpenHands rows explicitly, then feed all sections through
the existing provider and basis rollup representation.

### TCA-C2/C3/C4 — Reconciliation

Extend every LLM adapter with a conformance-tested `usage` verb. The engine calls
only the port. Align requested and ledger windows to provider UTC buckets, compare
only reported-basis dollars, state the reconcilable share, and never auto-correct.
Persist the latest result, notify above configured drift, and expose
`not reconciled`, `reconciled/no drift`, and `reconciled/drift` distinctly.

## Review and delivery gate

Every iteration selects one row above, refreshes its acceptance mapping, adds
focused behavioral/adversarial coverage, runs the broadest practical suite,
performs per-file and cross-file review, stages exact files, passes
`git diff --cached --check`, commits with the TCA item ID, pushes, and verifies
HEAD/upstream/remote parity before the loop advances.

## Product decisions and residual risks

- Q1 is implemented as the current run-record KEEP value, per the PRD delivery
  plan; Finance can request a longer independent retention later.
- Q2 is implemented conservatively: statements group exact keys only and do not
  infer PR/ticket identity.
- A same-label retry is one union identity but may represent multiple provider
  calls. TCA-A1 records the attempt count and compatible aggregate; TCA-A3 must
  preserve that aggregate when a completed run record wins.

## TCA-A1 validation evidence

Post-review accounting/lifecycle checks passed (88), the expanded
budget/provider/draft-stop/state suite passed (121), and the full registry
compatibility suite passed (1,710). TCA-A1 does not migrate historical spend
readers; that single-source union remains TCA-A3 as required by S3.
