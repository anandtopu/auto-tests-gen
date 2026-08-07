# B4 Verdict Surfaces — Per-file Analysis

Date: 2026-08-06
Scope: PRD v2 B4 implementation on codex/test-knowledge-a1-a2
Status: Complete; no P0/P1/P2 finding remains open

## Pass 1 inventory

| File | Review result |
| --- | --- |
| engine/lib/test_reviewer.py | Canonical projection, policy snapshot, legacy handling, bounded comment text, malformed contracts, and total disabled/unavailable states reviewed. Unsafe persisted loop/text values were hardened. |
| engine/lib/run_record.py | Required review block is always written and remains independent of gate-derived overall. A present malformed contract now records unavailable rather than disabled. |
| engine/lib/pr_comment.py | Live and historical paths use the shared formatter; no-impact silence remains intact. |
| engine/pipeline.sh | Disabled review is a deterministic skip and the bounded line is appended before JIRA delivery. Gate ordering and independence are unchanged. |
| engine/lib/run_progress.py | Agent review appears after validate and before quality gate for every executable chain; legacy B1 records remain readable. |
| engine/lib/wizard_status.py | Agent evidence is a distinct step and cannot substitute for Team review. |
| engine/lib/explain.py | Findings, fixes, repair count, survivors, and policy come only from recorded evidence; malformed loop data cannot crash rendering. |
| bin/dashboard.py and bin/qa.py | Both human review boards show agent context in a separate column without invoking a status transition. The initially omitted CLI column was added. |
| registry/tests/test_reviewer_surfaces.py and related suites | Canonical, cross-surface, ordering, adversarial, legacy, and human-state isolation coverage reviewed. |
| user, architecture, PRD-plan, and B4-plan docs | Runtime semantics, non-goals, validation evidence, and next backlog item agree. |

AGENTS.md contains an unrelated generated timestamp change and is explicitly
excluded from this iteration and staging.

## Findings fixed

| Priority | Finding | Resolution |
| --- | --- | --- |
| P2 | A malformed persisted `loops` value could raise in comments or explain and take down an otherwise valid historical surface. | Parse non-negative integer loops defensively and default malformed evidence to zero without inventing repairs. |
| P2 | A present but malformed reviewer contract loaded as `None`, which could be mislabelled skipped when the default flag was off. | Presence of the contract forces unavailable classification; genuine absence still follows enabled/disabled state. |
| P2 | Disabled review had no phase-skip evidence, so live Run progress could show the reviewer as running while the critic was active. | Record a deterministic `review` skip before returning from REVIEW_TESTS. |
| P2 | The dashboard had the requested board column but the canonical `make reviews` board did not. | Add the same latest-run verdict/unresolved context to the CLI board. |
| P3 | Persisted policy/verdict text could carry comment line breaks. | Bound and flatten both fields in the shared formatter. |

No actionable per-file finding remains open.
