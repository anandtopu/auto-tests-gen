# B6 Reviewer Evaluation — Per-file Analysis

Date: 2026-08-06
Scope: PRD v2 B6 reviewer attack evaluation
Result: No open P0, P1, or P2 finding

| File | Review result |
| --- | --- |
| `eval/reviewer/v1/fixtures.json` | All four closed reviewer categories are attacked once and the clean control is behaviorally complete. Generated file/test identifiers are present in the model-visible context. |
| `eval/reviewer/v1/labels.json` | QE ownership, fixed M3 threshold, fixture SHA-256, exact verdict/category, and grounded file/test/evidence terms are explicit. |
| `eval/reviewer_quality.py` | Fixture loading fails closed on type, path, identity, hash, category, threshold, size, and contract errors. Scripted and real evidence are scored separately; real mode is explicit and mock-marked results cannot become measured evidence. |
| `eval/scorecard.py` | Both evidence states render by name. Simulated per-class rates and the clean control are visible; a measured real run renders rate, clean outcome, provider, and model. Blocked real evidence carries the reason. |
| `registry/tests/test_reviewer_quality.py` | Attacks hash drift, path traversal, lowered thresholds, malformed contracts, wrong classes, ungrounded lucky classes, clean false positives, simulated-output laundering, real misses, CLI failure, and Makefile/scorecard wiring. |
| `Makefile` | Deterministic evaluation is part of `eval` and `review`; the billable real evaluator is a separate explicit target and cannot run transitively. |
| `docs/pr-jira-fused-context-b6-implementation-plan.md` | Acceptance criteria, boundary, validation, real-auth blocker, and next dependency are mapped. |
| `docs/prd-pr-jira-fused-context-implementation-plan.md` | B6 is completed only with test evidence; B2 becomes next eligible. |
| `docs/pr-jira-fused-context-b4-implementation-plan.md` | Residual-work statement no longer incorrectly calls B6 pending. |
| `docs/architecture.md`, `docs/user-guide.md`, `CLAUDE.md`, `REVIEW.md`, `docs/multi-llm-providers.md` | Operator surfaces consistently distinguish simulated plumbing from blocked/measured real-model judgement and update the parity-claim count. |

## Findings fixed during pass 1

1. The first oracle accepted a correct category without proving the finding
   referred to the seeded defect. Labels now require the expected file/test and
   fixture-specific evidence terms; a lucky-category mutation is pinned.
2. Label/fixture top-level and nested type errors could raise incidental
   exceptions rather than the evaluator's bounded fixture error. Structural
   validation now rejects them deliberately.
3. A real output carrying `simulated: true` could have been passed to the
   scoring API as measured evidence. Real scoring now rejects every such row,
   and an anti-laundering test proves the boundary.
