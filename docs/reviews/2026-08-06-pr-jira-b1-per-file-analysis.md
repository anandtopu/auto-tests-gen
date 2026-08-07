# B1 Test Reviewer — Per-file Analysis

Date: 2026-08-06
Scope: PRD v2 B1 implementation on codex/test-knowledge-a1-a2
Status: Complete; no P0/P1/P2 finding remains open

## Pass 1 inventory

| File | Review result |
| --- | --- |
| engine/lib/test_reviewer.py | Source confinement, bounded input/contract size, strict enums, verdict consistency, repo isolation, total loading, and aggregation reviewed. Unsafe repo names and shallow merged reload were fixed. |
| engine/pipeline.sh | Placement after validate and before gate, per-repo inputs, zero-test skip, nonfatal failure, labels, spend capture, and feature-off cleanup reviewed. Global merge fallback consistency was fixed. |
| prompts/test-reviewer.md | Prompt injection boundary, read-only role, prohibited revalidation, closed semantic scope, and exact output shape reviewed. |
| engine/phases/contracts/reviewer.schema.json | Extraction minimum is paired with strict post-extraction validation; no model field is trusted for routing. |
| engine/phases/mock_phase.sh | Approve, needs_work, malformed, and simulated labels reviewed. Unknown scripted verdicts now fail rather than silently approve. |
| registry/org-config.yaml | Phase/model inventory parity, Read-only tools, default-off flag, and reserved B2/B3 policy fields reviewed. |
| engine/lib/llm_runner.py | Reviewer inventory registration reviewed; phase remains completion-compatible because source is inlined. |
| engine/lib/run_record.py | Durable top-level reviewer evidence is independent from gate-derived overall. |
| engine/lib/settings_store.py | Default-off user toggle and nonfatal consequence language reviewed. |
| .env.example and aiqe.properties.example | Both supported configuration examples now carry the reviewer toggle. |
| registry/tests/test_test_reviewer.py | Happy, mixed-repo, path traversal, invalid repo, malformed contract, outage, skip, mock, and gate-independence coverage reviewed. |
| registry/tests/test_task_bundle.py | The new reviewer phase-input archive boundary is pinned. |
| docs/architecture.md and docs/user-guide.md | Runtime boundary, defaults, non-goals, and rollout ownership reviewed. |
| docs/prd-pr-jira-fused-context-implementation-plan.md and B1 detailed plan | PRD acceptance mapping, evidence, residual work, and next item reviewed. |

AGENTS.md contains a pre-existing generated timestamp change and is explicitly
outside this iteration; it is neither modified nor staged by B1.

## Findings fixed

| Priority | Finding | Resolution |
| --- | --- | --- |
| P2 | A malicious repository name could be reused in reviewer output paths. | Enforce a bounded safe repository grammar in preparation, normalization, and merge; invalid rows become unavailable. |
| P2 | Run-record loading validated only the merged top level and could accept inconsistent nested evidence. | Revalidate every repo, finding, aggregate verdict, flattened findings, schema, and simulated label before exposure. |
| P2 | The global merge-failure fallback had unavailable with no repo rows, which strict loading would classify inconsistently. | Persist one explicit reviewer/unavailable row with a reason. |
| P3 | An unknown mock verdict silently selected approve. | Accept only approve or needs_work; unknown scripts fail with exit 64. |
| P3 | The Settings toggle was missing from aiqe.properties.example. | Add the field and pin it through the existing settings-example parity test. |

No actionable per-file finding remains open.
