# B4 Implementation Plan — Reviewer Verdict Surfaces

Date: 2026-08-06
Status: Implemented, validated, and reviewed
PRD: docs/prd-pr-jira-fused-context-multi-agent.md §5 B4
Branch: codex/test-knowledge-a1-a2

## Acceptance mapping

| Requirement | Implementation | Verification |
| --- | --- | --- |
| Durable verdict block | Every completed run records canonical `review` evidence with verdict, findings, loops, unresolved findings, and the effective `agent_gate` policy. Disabled, unavailable, and malformed-contract states are explicit. | Snapshot, run-record, malformed, disabled, and unavailable tests |
| Review-board column | The dashboard Recent runs board and `make reviews` CLI show the latest agent verdict/unresolved count beside, never instead of, human status. | Generated-dashboard and CLI source/behavior tests |
| PR and JIRA comments | PR coverage markdown and the common JIRA run summary use one bounded formatter for the same snapshot. | Live/historical PR parity and pipeline-order tests |
| Wizard and Run progress | Both surfaces add `Agent review` after generated-test validation and before `Quality gate`; verdict details remain visible for approve, needs_work, skipped, and unavailable. | Wizard and historical/live progress tests |
| Explain | `make explain` lists recorded findings and named fixes, repair-loop count, unresolved survivors, verdict, and policy without inventing missing evidence. | Persisted-record explain test and malformed-loop adversarial test |
| B4.1 human decision isolation | Review renderers only read run evidence. Human `approved`/`changes_requested` remains owned exclusively by explicit `review_state.set_status` calls. | Isolation pin plus full review-state compatibility suite |

## Implementation boundary

- `engine/lib/test_reviewer.py` owns the canonical projection and bounded
  summary line. B1 phase contracts remain the durable raw evidence.
- B2 now populates `loops`, iteration evidence, fixes, validation results, and
  surviving `unresolved` findings. Runs without a repair retain the original
  zero-loop representation.
- B3 has not shipped: the policy is recorded for observability but does not
  alter gate outcome. Run `overall` remains gate-derived.
- Old B1 records remain readable and show `policy: not_recorded`; historical
  evidence is never rewritten or relabelled with today's policy.
- No adapter, database, migration, dependency, container, or gate change is
  introduced.

## Validation evidence

- Focused surface/reviewer/UI/progress/wizard/CLI suite: 93 passed.
- Cross-file compatibility suite: 83 passed.
- Full registry suite: 1,495 passed in 728.64 seconds.
- Python compilation and Git Bash syntax validation passed. Ruff passed for all
  touched files with the repository's known legacy-style diagnostics excluded;
  an unfiltered run still reports those pre-existing E401/E402/E702/E731/F401
  patterns. The staged whitespace check remains mandatory before commit.
- Two-pass review fixed malformed persisted-loop rendering, malformed on-disk
  reviewer classification, disabled live-progress ambiguity, and the initially
  missing CLI board column. No P0, P1, or P2 finding remains open.

## Residual work

B6 owns seeded-defect catch rates, the clean-control check, and strict simulated
versus real-model labelling. B2 now populates repair-loop evidence. B3 is next
and enforces `off|warn|require`, while B4 only records and renders those facts.
