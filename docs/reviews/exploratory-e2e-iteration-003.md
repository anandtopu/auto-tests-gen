# Exploratory E2E Iteration 003 Review

## Scope

- Feature slice: Guided run — JIRA plan-first author, approve, generate, and
  ticket-link journey.
- Runtime: served dashboard in mock-adapter mode with deterministic `PROJ-301`.
- Changed files: wizard status aggregation, queue execution, their focused
  regression tests, exploratory status matrix, and this review.

## Findings

| ID | Severity | File | Finding | Impact | Fix |
| --- | --- | --- | --- | --- | --- |
| E2E-EXP-003 | P2 | `engine/lib/wizard_status.py` | A re-authored draft with `generated_run: null` still exposed an older run id, committed overall result, and agent review. | Reviewers could believe the new unapproved draft had already generated and committed tests. | Resolve every generated-result field from the single `generated_run` reference; clear all evidence when absent or dangling. |
| E2E-EXP-004 | P2 | `engine/lib/work_queue.py` | Dashboard queue subprocesses passed native Windows PATH directly to Git Bash, selecting an unexecutable WindowsApps `python3` shim. | The documented local JIRA generation journey failed with exit 127 after approval. | Use `git_bash_command` and prepend the active interpreter directory before launching the pipeline. |

## Pass 1 — Per-file review

- `engine/lib/wizard_status.py`: run id, overall status, tests, agent review, and
  gates now share one correlation source. PR mode keeps historical run inference
  because PRs without plans have no `generated_run` pointer. Missing run records
  fail closed instead of falling back to an unrelated latest run.
- `engine/lib/work_queue.py`: PR, PR-plan, JIRA, plan, and tests arguments remain
  unchanged. Inline-ticket and ticket-link environment variables are assembled
  before normalization and survive into the child. The active interpreter is
  prepended only for the pipeline subprocess.
- `registry/tests/test_wizard.py`: the regression constructs a draft plus an old
  committed run and verifies the complete stale evidence set is hidden.
- `registry/tests/test_work_queue.py`: the regression pins both the normalized
  Bash wrapper and project-interpreter precedence while checking queue completion.
- Documentation: evidence identifies synthetic fixtures and does not claim
  production adapter or provider coverage.

## Pass 2 — Cross-file integration review

- UI → API → state: malformed inputs still terminate at server validation;
  `/api/plans/generate` continues to enforce approval before queue creation; the
  wizard reads the run explicitly recorded by `plan_state.mark_generated`.
- Queue → Bash → Python: the runner reuses the repository's established MSYS
  PATH conversion, and the browser retest proved `pipeline.sh tests PROJ-301`
  can reach generation and the gate from the dashboard process.
- Security: only mock adapters and synthetic ticket data were used. The fix does
  not weaken key validation, approval, secret scanning, repository scope, or gate
  policy, and it does not add command-string interpolation.
- Reliability: dangling run references and torn/missing historical records
  degrade to pending evidence; background failures retain their actionable queue
  reason. Queue locking and single-run serialization are unchanged.
- Deployment: no schema, migration, port, service, or manifest changes. POSIX
  behavior remains `bash` plus inherited `python3`; Windows chooses the active
  project interpreter through the existing Git Bash boundary.
- Coverage: both observed failures have focused regressions at their composition
  boundaries, plus real browser/pipeline retests.

## Validation

- Before fix: stale-status regression returned `old-generated-run`; queue-runner
  regression showed direct `bash.exe engine/pipeline.sh` invocation and then no
  interpreter prepend.
- After fix: `registry/tests/test_wizard.py` and
  `registry/tests/test_work_queue.py` — 19 passed.
- Adjacent compatibility: `test_plan_first_journey.py`, `test_ui_features.py`,
  and `test_env_flag.py` — 47 passed.
- Live browser: empty and malformed ticket rejection passed; draft generation
  was blocked; author and approval transitions persisted; generation created one
  API spec, committed `e2e-api-tests-1`, and the mock JIRA link action completed.
- Original Windows failure was reproduced as exit 127 with the WindowsApps path,
  then the same failed queue item completed through the CLI isolation check and
  a fresh dashboard generation completed after interpreter precedence was fixed.

## Action Plan

| Priority | Owner | Action | Acceptance check |
| --- | --- | --- | --- |
| Next iteration | exploratory loop | Exercise Intake and work queue lifecycle using isolated synthetic items. | Release fetch, inline/plan intake, requeue/remove, drain, and failure details agree across UI and persisted queue state. |

## Open Questions

- Low-risk UX observation: while a repeat generation is running, the ladder can
  continue showing the last completed generated artifacts under a global
  `Working…` banner. Treat as historical-progress presentation unless user
  testing shows operators misattribute it to the in-flight run.
