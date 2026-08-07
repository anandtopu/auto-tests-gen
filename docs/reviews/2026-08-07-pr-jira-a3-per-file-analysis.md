# A3 Plan-first from PR - Pass 1 per-file review

Date: 2026-08-07
Scope: PRD A3 implementation only

| File | Responsibility | Review result |
| --- | --- | --- |
| `engine/pipeline.sh` | PR-plan authoring/resume orchestration and delivery | Pass after aligning direct PR-number validation and retaining the stored ticket for resumed delivery. Plan exit remains before run-record/gate code. |
| `engine/lib/plan_state.py` | Existing lifecycle plus PR resume metadata | Pass. Target identity is validated against the PR key; approval, edit revocation, signing, adversary evidence, and generated-run state remain shared. |
| `engine/lib/work_queue.py` | Intake, dedupe, identity, execution | Pass. Flag-off refuses; PR plan numbers/repos/tickets use PR validation; command carries the PR number; effective cost key is the PR key. |
| `engine/gate/spec_check.py` | Signed-spec enforcement policy | Pass. A3.6 is explicit: unsigned/free-form PR keys exempt, signed structured PR keys enforced. |
| `bin/dashboard_server.py` | Queue URL intake and wizard status API | Pass. PR-plan URLs and `pr-plan` read mode are bounded by existing validation. |
| `bin/dashboard.py` | Wizard and release-queue entry points | Pass after hiding controls when the flag is off and fixing dynamic queue PR-plan identity. |
| `engine/lib/wizard_status.py` | Shared approval/generation ladder | Pass. `pr-plan` reads the same plan and generated-run facts as ticket plan-first. |
| `engine/lib/settings_store.py`, `.env.example`, `aiqe.properties.example` | Flag declaration | Pass. All supported surfaces declare `AIQE_PR_PLAN=0`. |
| `registry/tests/test_pr_plan.py` | Acceptance/adversarial coverage | Pass. Covers A3.1-A3.6, flag parity, queue identity, dual delivery, no run record, signed/unsigned policy, and UI gating. |
| PRD, implementation, architecture, user docs | Product and operator contract | Pass. Invocation, governance decisions, rollout, and completion status agree with code. |

No unresolved P0/P1/P2 finding remains in the reviewed files.
