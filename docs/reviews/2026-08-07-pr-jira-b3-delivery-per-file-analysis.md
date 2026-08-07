# PR/JIRA B3 Delivery Policy — Per-File Review

Date: 2026-08-07
Branch: `codex/test-knowledge-a1-a2`
Scope: B3 `off|warn|require`, refusal evidence/surfaces, C14, and tests.

## Pass 1 — file-by-file findings

| File(s) | Review result |
| --- | --- |
| `engine/lib/test_reviewer.py` | Closed policy/outage values, strict delivery normalization, bounded single-line summaries, and require-over-env precedence reviewed. Tampered proceed cannot launder required needs-work. |
| `engine/pipeline.sh` | Decision is after repair and before critic/gate. Refusal persists first, comments with fixes, sets PR failure, never calls human review transition, and exits 78. Default disabled review produces no delivery artifact. |
| `engine/lib/run_record.py` | Run-relative strict delivery evidence produces `review_refused` with empty gates. P2 wrong-root lookup found and fixed. Critic remains unable to affect overall. |
| `engine/lib/pr_comment.py` | Refusal and bounded fixes render on live/historical PR comments without claiming a commit. |
| `engine/lib/run_progress.py`, `engine/lib/wizard_status.py` | Exit 78 is named; Agent review is failed and Quality gate skipped/blocked, never passed or unknown. |
| `engine/lib/team_report.py`, `bin/qa.py`, `bin/dashboard.py`, `eval/scorecard.py` | `review_refused` is a first-class failure category. P2 false no-changes classification found and fixed across summaries, attention, release rollups, and scorecard counts. |
| `specs/platform/constitution.yaml`, `CLAUDE.md` | C14 pins each authority boundary; C2 critic rule remains distinct and unchanged in meaning. |
| `docs/architecture.md`, `docs/user-guide.md`, `docs/ui-guide.md`, `docs/use-cases.md` | Runtime order, exit 78, estate-only override, outage policy, and warn-then-require rollout now match code. |
| `registry/tests/test_reviewer_delivery.py` | Policy matrix, tampering, newline hardening, boundary, no-bypass, run-record/comment/progress, C14, Settings, and shell syntax covered. |
| `registry/tests/test_team_report.py`, `registry/tests/test_testcase_learning.py` | Refusal cannot become no-changes; normal post-gate learning remains before normal finalization while early refusal correctly records before learning. |
| B3/master implementation plans | Acceptance mapping and delivery order reconciled to repository evidence. |

## Findings fixed

- **B3-R1 (P2):** run records initially read module-root refusal evidence, which could associate the wrong run. Fixed with an explicit run-relative path and integration test.
- **B3-R2 (P2):** team/report surfaces treated the new outcome as no changes. Fixed with a distinct refusal category everywhere the run appears.
- **B3-R3 (P2):** refusal summary fixes could retain embedded newlines. Fixed with bounded single-line rendering.
- **B3-R4 (P2):** default disabled review initially wrote a new delivery artifact. Fixed by invoking delivery only when current-run reviewer evidence exists; require forces that evidence.
- **B3-R5 (P3):** a pre-existing learning-order pin assumed one run-record call. Updated to distinguish early refusal from normal post-gate finalization without weakening the original invariant.

No open P0–P2 per-file finding remains.

## Validation

- Final focused reviewer/repair/learning/report/constitution group: 101 passed.
- Final full registry suite: 1,559 passed in 829.88 seconds.
- Fatal Python lint selection (`E9,F63,F7,F82`): passed on every B3-touched Python file.
