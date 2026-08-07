# Exploratory E2E Iteration 002 Review

## Scope

- Feature slice: Guided run — Pull request to E2E tests.
- Runtime: served dashboard in mock-adapter mode with the deterministic
  `orders-api#201` fixture.
- Changed files: `bin/dashboard.py`, `registry/tests/test_wizard.py`, user guide,
  exploratory status matrix, and this review.

## Findings

| ID | Severity | File | Finding | Impact | Fix |
| --- | --- | --- | --- | --- | --- |
| E2E-EXP-002 | P2 | `bin/dashboard.py` | Changing to a rejected second PR left the previous PR's ladder and generated artifacts visible. | Operators could attribute committed evidence to the wrong target. | Clear keyed results on every target edit and flow change. |
| E2E-EXP-002-R1 | P2 | `bin/dashboard.py` | A status request already in flight could repaint the old target after a reset. | The initial clear could be undone nondeterministically on slow networks. | Guard responses with the captured revision, key, and mode. |
| E2E-EXP-002-R2 | P2 | `bin/dashboard.py` | The form could change while enqueue awaited its response, before polling had a revision to guard. | An old submission could resume against newly edited inputs and launch stale polling. | Lock PR target controls only during enqueue/start, then re-enable for the background run. |

## Pass 1 — Per-file review

- `bin/dashboard.py`: the reset owns UI-only state, cancels scheduled work, and
  does not alter API contracts. The temporary control lock is released from
  `finally` on success and failure for direct and plan-first PR intake.
- `registry/tests/test_wizard.py`: the regression pin renders the actual page and
  verifies every target field is wired, both result containers clear, late-poll
  invalidation exists, and both PR intake handlers release their lock.
- Documentation: records target/result ownership without promising synchronous
  generation or changing the plan-first approval invariant.

## Pass 2 — Cross-file integration review

- UI → API: `/api/queue`, `/api/queue/run`, and `/api/wizard/status` are unchanged;
  invalid repo/PR/ticket inputs still fail at server intake before pipeline work.
- Async reliability: edits after submission invalidate already-issued status
  requests; edits cannot interleave with the two short intake requests; controls
  become editable again while the background run proceeds.
- Security: synthetic inputs only; no credential fields or external production
  systems were touched. Existing escaping and server validation remain intact.
- Deployment: browser-only code is emitted by the existing dashboard generator;
  no schema, configuration, migration, or service restart requirement was added.

## Validation

- Before fix: focused regression failed because `wzResetTarget` was absent.
- After fix: `test_wizard.py` plus `test_wizard_plan_coherence.py` — 14 passed.
- Adjacent dashboard/documentation checks — 33 passed.
- Broad gate: 1,574 pytest checks passed with 70.19% branch-aware coverage;
  adapter conformance, all adversarial suites, bootstrap/entrypoint smoke,
  benchmark replay, context/discovery/retrieval/reviewer quality, and scorecard
  checks passed. `make review` could not launch Windows' WSL Bash after pytest
  (`HCS_E_CONNECTION_TIMEOUT`), so the exact remaining scripts were completed
  successfully with `C:\\Program Files\\Git\\bin\\bash.exe`. Real-model reviewer
  quality remains explicitly blocked on provider authentication, as before.
- Live browser: mock PR happy path committed; five invalid/boundary cases rejected;
  original stale-result sequence passed after fix; immediate target edit during
  enqueue/poll stayed clear; zero browser warnings/errors.

## Action Plan

| Priority | Owner | Action | Acceptance check |
| --- | --- | --- | --- |
| Next iteration | exploratory loop | Exercise Guided JIRA plan-first author/approve/generate/link sequencing. | Human approval remains blocking, invalid keys fail safely, target changes cannot retain another ticket's ladder. |

## Open Questions

- None blocking this iteration.
