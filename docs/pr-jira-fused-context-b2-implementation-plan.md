# B2 Implementation Plan — Bounded Repair from Reviewer Findings

Date: 2026-08-06
Status: Implemented
PRD: docs/prd-pr-jira-fused-context-multi-agent.md §6 B2
Branch: codex/test-knowledge-a1-a2

## Acceptance mapping

| Requirement | Planned implementation | Acceptance checks |
| --- | --- | --- |
| B2 bounded findings-driven repair | After the initial B1 fan-out review, only repositories with unresolved findings enter `REPAIR_FROM_REVIEW`; each receives its own findings/test-source bundle and target-repo confinement. | Approve/no-test/unavailable paths make zero repair calls; needs-work selects only affected repos. |
| B2.1 `review.max_loops` | Normalize the existing org-config value, default 1, and stop when approved, no unresolved repo remains, or the cap is reached. Validation and re-review happen inside every completed loop. | Zero/default/custom/invalid configuration tests; exact loop-count pipeline tests. |
| B2.2 metering and iteration evidence | Add the agentic `reviewrepair` phase with unique iteration/repo labels. Repair, re-validation, and re-review all pass through budget recording; the final reviewer contract carries every iteration's findings, applied fixes, validation, and verdict into the run record. | Phase-inventory, cost-label/order, history normalization, run-record, and summary tests. |
| B2.3 unresolved survival | A finding is cleared only when a valid repair contract addresses its index and re-review does not raise the same identity. Unaddressed findings are carried even if a later reviewer returns approve. | No-op repair, repeated finding, omitted-finding laundering, and max-loop exhaustion tests. |
| B2.4 named confined repair | Add `prompts/review-repair.md`, a strict contract/schema, Sonnet authoring tier, `Read,Edit` tools, and the same `AIQE_TARGET_REPO`/per-repo conventions and catalog slice used by generation/reviewer fan-out. Repair may edit only generated test files already registered for that repo; validate executes immediately afterward. | Tool/capability pins, path traversal/cross-repo rejection, prompt contract, mock side-effect, and pipeline ordering tests. |

## Design boundary

- The existing `AIQE_TEST_REVIEWER`/`review.enabled` flag remains the rollout
  boundary. Reviewer off means repair off. B3 delivery enforcement is not part
  of this item: final needs-work still warns and proceeds to the deterministic
  gate.
- Initial B1 reviewer failure remains non-fatal. Once a repair phase starts it
  is a write-enabled delivery phase: phase/contract/validation failure is fatal
  before the gate, so partially repaired files cannot be committed as success.
- The repair phase edits existing generated specs only. Missing coverage is
  added to an already generated spec; creating arbitrary new files would evade
  the B1 source list and is rejected by the repair contract boundary.
- `validate` remains the sole executor. The repair phase does not run tests and
  cannot claim a fix passed.
- The reviewer, critic, human review state, and deterministic gate remain
  distinct. This item does not implement `off|warn|require` enforcement.

## Validation evidence

- Focused B2 plus Windows durability regression: 23 passed, including a full
  mock pipeline that executes exactly one repair/validate/rereview loop.
- Reviewer, cache, run-record, UI/comment surface, progress, and task-bundle
  compatibility matrix: 225 passed.
- Full registry suite: 1,534 passed in 767.11 seconds.
- Focused Ruff fatal/error rules, Python compilation, and Git Bash syntax checks
  passed. `git diff --check` passed before staging.
- The two-pass review fixed actual-edit-set binding, nested-evidence validation,
  apply-time contract revalidation, phase-cache exclusion, malformed-mock
  control flow, and Windows-safe atomic history writes. Reports:
  [per-file analysis](reviews/2026-08-06-pr-jira-b2-repair-per-file-analysis.md),
  [integration review](reviews/2026-08-06-pr-jira-b2-repair-integration-review.md),
  and [action plan](reviews/2026-08-06-pr-jira-b2-repair-action-plan.md).

## Residual work

B3 is next. It gives the already recorded `off|warn|require` policy a pre-gate
delivery consequence and pins the constitution amendment; B2 remains advisory
when its bounded loop ends with unresolved findings. Real-model reviewer and
repair quality remains unmeasured until parity authentication is restored.
