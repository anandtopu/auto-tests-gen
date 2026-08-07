# B4 Verdict Surfaces — Cross-file Integration Review

Date: 2026-08-06
Status: Complete

## Correctness

- The B1 merged contract is projected once into the PRD's canonical review
  block; comments, progress, boards, and explain use that same evidence shape.
- Effective policy is captured at run finalization. Legacy records explicitly
  report that policy was not recorded rather than borrowing current config.
- Wizard and Run progress order Agent review after validation and before the
  gate. Disabled, unavailable, needs_work, and approve remain distinct.
- JIRA receives the review line through the existing common summary; PRs use
  the coverage-comment port. No new external delivery path exists.

## Security and governance

- Comment fields are bounded and line-flattened; finding details rendered by
  explain are length-bounded and never interpreted as instructions.
- A machine verdict is read-only context. Dashboard, CLI, comments, wizard,
  progress, and explain do not call `review_state.set_status`.
- Gate code still never reads reviewer output, and run `overall` is unchanged.

## Reliability and deployment

- Missing reviewer evidence becomes skipped only when disabled; enabled or
  malformed evidence becomes unavailable. Neither state aborts delivery.
- Old B1 records remain viewable without migration. New runs always carry the
  canonical block, including default-off runs.
- No schema migration, service port, dependency, container, credential, or
  adapter change is introduced.

## Coverage and validation

- Focused B4 and touched-surface suite: 93 passed.
- Related run-record/review/trace/spend/task-boundary suite: 83 passed.
- Full registry suite: 1,495 passed in 728.64 seconds.
- Python compilation and Git Bash syntax checks succeeded. Ruff passed on all
  touched files with the repository's known legacy-style diagnostics excluded;
  the unfiltered run still reports those pre-existing patterns.

## Residual risks

- B4 exposes mock reviewer verdicts but does not establish their quality; B6
  owns seeded-defect and clean-control evaluation.
- B2 owns mutation/repair and will replace the initial zero-loop/all-unresolved
  projection with measured loop history.
- B3 owns delivery consequences. `policy` is observable now but advisory.

No P0, P1, or P2 cross-file finding remains open.
