# B2 Task Bundle — Per-File Review

## Scope

PRD B2 implementation on `codex/test-knowledge-a1-a2`: phase capture, task
manifest finalization, run-record/explain integration, portable-state behavior,
locking, tests, and operator/architecture documentation.

## Findings

| ID | Severity | File | Finding | Resolution |
| --- | --- | --- | --- | --- |
| B2-R1 | P1 | `engine/lib/task_bundle.py` | A fixed scratch journal could combine evidence from concurrent runs in one checkout. | Journal names are now derived from the full run-id hash and their run/key ownership is validated. Adversarial two-run isolation is pinned. **Fixed.** |
| B2-R2 | P1 | `engine/lib/explain.py`, `engine/lib/task_bundle.py` | An intact pointer to another run's bundle could answer a historical explain request with valid but wrong evidence. | Historical resolution now requires manifest run ID and key to match the selected run record. **Fixed.** |
| B2-R3 | P2 | `engine/lib/task_bundle.py` | Any input named `AGENTS.md`, or a failed estate write, could be labeled a successful full-estate fallback. | Fallback requires the exact estate guidance path and a produced archive reference; failure remains unavailable. **Fixed.** |
| B2-R4 | P2 | `engine/lib/fs_lock.py` | A transient Windows owner-marker unlink failure left a live-PID orphan that could wedge B1 writers. | Retry only marker deletion; retain the measured single-shot directory removal rule. **Fixed.** |

## Validation

- New B2 implementation and adversarial tests pass Ruff.
- The focused B2 surface passes 98 tests; the concurrent writer test also passes
  five consecutive repetitions.
- Generated scratch and runtime store contents are excluded from the review diff.

## Residual

- Plan-only lifecycle runs do not create generation run records in the existing
  architecture; B2 attaches to run-record-producing pipeline executions.
- Artifact retention remains governed by B1's independently configurable run window.
