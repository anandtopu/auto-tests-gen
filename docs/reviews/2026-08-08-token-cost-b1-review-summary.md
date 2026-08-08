# Review Summary: TCA-B1 Complete Consumer Report

Date: 2026-08-08
Reviewer: Codex
Branch/Commit: `codex/test-knowledge-a1-a2` (pre-commit review)

## Overall status

| Area | Status | Notes |
|---|---|---|
| Per-file pass | Completed | Report, embedding ledger, probe shell, Cost view, tests and docs reviewed |
| Cross-file pass | Completed | User/probe/embedding/unknown paths traced through durable sources and surfaces |
| Release review | Ready | Eight actionable findings fixed; no vendor call or new service |
| Validation | Passing with one unrelated transient | 110 focused/adjacent; 199 compatibility; two full sweeps each 1,738 pass; transient plan test passed alone; Git Bash syntax clean |

## Completed scope

- B1.1: basis-aware daily embedding rows, compatible with scalar days.
- B1.2: probe calls use normal metering/history and non-user attribution.
- B1.3: numeric unmeterable phase/task line, including zero.
- B1.4: one all-consumer provider/basis rollup implementation.

## Findings fixed

The review fixed missing probe durability/attribution, absent unknown-task
counts, shared-scratch concurrency, unlocked embedding RMW, badge pollution,
legacy embedding evidence handling, and vector-store test isolation. See the
action register.

## Residual risk

Historical scalar embedding entries cannot reveal original call/token counts;
those cells stay explicitly unknown. The intentionally billed probe was not run
without operator credentials.
The second full registry sweep's unrelated mock plan-arbitration test failed
after passing in the first sweep and then passed alone; it is recorded rather
than misreported as a clean full-suite exit.

## Next action

After exact staging, broad verification, commit and push, advance to TCA-C2:
provider usage through the adapter port.
