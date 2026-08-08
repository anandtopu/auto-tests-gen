# Review Summary: Token-cost Accounting PRD and TCA-A1

Date: 2026-08-08
Reviewer: Codex
Branch/Commit: `codex/test-knowledge-a1-a2` (pre-commit review)

## Overall Status

| Area | Status | Notes |
|---|---|---|
| Per-file pass | Completed | PRD, pipeline, provider wrappers, live meter, path/state lifecycle, retention, and tests reviewed |
| Cross-file integration pass | Completed | Exit handler, marker boundary, relocation, bundle/reset/prune, and test isolation traced |
| Tests/build checks | Completed | 88 post-review focused, 121 expanded, and 1,710 full registry tests passed; lint/compile/Bash syntax passed |
| Release/demo readiness | Ready | TCA-A1 is default-on and preserves run-record metrics; later report/reconciliation slices remain planned |

## Findings

| ID | Severity | Status | Owner Area | Summary | Evidence | Recommended Action |
|---|---|---|---|---|---|---|
| TCA-R1 | P1 | Completed | Pipeline | One EXIT trap must flush then release without changing status | `_pipeline_exit` source and draft-stop functional test | Preserve the single-handler invariant |
| TCA-R2 | P1 | Completed | Accounting | Same-label retries need auditable consolidation | Two-attempt and mixed-basis fixtures | Preserve attempt counts and never blend bases |
| TCA-R3 | P1 | Completed | Provider boundary | Start markers must not classify cache hits as calls | Marker placement after caches, before provider invocation | Preserve provider-boundary placement |
| TCA-R4 | P2 | Completed | Test isolation | Default-on history must not write test traffic into estate state | `AIQE_COSTS_DIR` conftest redirect and full suite | Keep the class-level isolation pin |
| TCA-R5 | P1 | Open | Reporting | Historical consumers still read completed run records only | `cost_report.collect()` and PRD A1.2a | Implement TCA-A3 after exit-path proof |
| TCA-R6 | P1 | Open | Reconciliation | No provider-bill comparison exists yet | PRD G3/C2 | Implement TCA-C2–C4 through the adapter port |

## Completed Scope

- TCA-A1 durable spend ledger, chained exit flush, exact call-start evidence,
  attribution, storage relocation, atomic locking, bundle/reset/prune/git hygiene,
  and suite isolation.
- Integration defects found during review were fixed and retested.

## Incomplete Or Deferred Scope

- TCA-A2 exit-path sweep is the next eligible item.
- Unified history consumers, task statement, complete report, and reconciliation
  remain in their planned dependency order.

## Validation Evidence

| Check | Result | Notes |
|---|---|---|
| Post-review accounting/lifecycle suite | Pass | 88 passed |
| Expanded compatibility suite | Pass | 121 passed |
| Full registry suite | Pass | 1,710 passed in 767.61s |
| Ruff / Python compile / Bash syntax | Pass | New Python and changed shell entry points |
| Diff whitespace | Pass | `git diff --check` |

## Next Actions

1. Implement TCA-A2 instrumented five-mode and abort-path coverage proof.
2. Implement TCA-A3 unified spend accessor only after that proof is green.
