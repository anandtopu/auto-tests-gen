# Review Summary: TCA-C1 Per-task Cost Statement

Date: 2026-08-08
Reviewer: Codex
Branch/Commit: `codex/test-knowledge-a1-a2` (pre-commit review)

## Overall Status

| Area | Status | Notes |
|---|---|---|
| Per-file pass | Completed | Model, path resolver, Make/CLI/API/dashboard, docs and tests reviewed |
| Cross-file integration pass | Completed | History-to-statement-to-export/UI flows traced; metrics boundary preserved |
| Tests/build checks | Completed | Ruff/syntax clean; focused 151/15/26; full registry 1,734 |
| Release/demo readiness | Ready | Exact-key, read-only views; isolated locked atomic exports |

## Findings

| ID | Severity | Status | Owner Area | Summary | Evidence | Recommended Action |
|---|---|---|---|---|---|---|
| TCA-C1-R1 | P1 | Completed | Accounting | Missing priced amount read as zero | Review fixture | Explicit incomplete count |
| TCA-C1-R2 | P1 | Completed | Isolation | Export writer escaped test estate | First broad suite failure | Relocatable export path + redirect |
| TCA-C1-R3 | P2 | Completed | Performance | N-times history reload | Dashboard flow trace | Shared union snapshot |
| TCA-C1-R4 | P2 | Completed | UX | Artifacts output overwhelmed by statement | Real CLI evidence | Compact summary |
| TCA-C1-R5 | P2 | Completed | Security | Export/render control characters | Checklist review | Formula/Markdown sanitization |

## Completed Scope

- C1.1: all exact-key durable run/phase rows, attempts and attribution.
- C1.2: independently typed totals; no blended dollar total.
- C1.3: deterministic Markdown/CSV exports, one line per phase.
- Make, CLI, authenticated API and artifact-panel surfaces.

## Incomplete Or Deferred Scope

- Embedding/probe end-to-end attribution reporting remains TCA-B1.
- Provider usage reconciliation remains TCA-C2–C4.

## Validation Evidence

| Check | Result | Notes |
|---|---|---|
| Ruff + Python compile | Pass | New model/test and all changed Python surfaces |
| Targeted/adjacent suite | Pass | 151 tests |
| Post-review focused suite | Pass | 15 tests |
| Export isolation/path suite | Pass | 26 tests |
| Full registry suite | Pass | 1,734 passed in 805.99s |

## Next Actions

1. Commit and push TCA-C1 after exact staged-diff checks.
2. Advance to TCA-B1 complete consumer report.
