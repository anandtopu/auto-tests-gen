# Exploratory E2E Review — Iteration 005

## Scope

The planned slice was Run progress and Runs & reviews. Browser coverage reached
committed/quarantined/unknown/malformed progress, failure logs, explanations, and
retry queueing. Review filters and decisions were stopped after the demo-data CLI
incident described below. This report covers the three fixes kept from the run.

## Findings

| ID | Severity | File | Finding | Impact | Fix |
| --- | --- | --- | --- | --- | --- |
| E2E-EXP-008 | P2 | `bin/dashboard.py` | Unknown/error responses retained prior-run actions/evidence. | Operators could retry the wrong historical failure while viewing a new key. | Clear all result panels and set a load-failed source badge before rendering no-result/error state. |
| E2E-EXP-009 | P2 | `bin/dashboard.py` | Successful retry refresh erased its confirmation and re-enabled retry. | An operator could submit duplicate work because success looked like no action occurred. | Reapply confirmation to the refreshed nodes and disable the replacement button. |
| E2E-EXP-010 | P1 | `engine/lib/demo_data.py` | `--help` was treated as the destructive default action. | A documentation request deleted generated runtime evidence. | Parse every CLI flag before `clear()`; help and invalid arguments now exit before any mutation. |

## Incident evidence and recovery

- Before invocation, the history query reported 593 committed run records.
- `demo_data.py --help` began clearing and then raised `PermissionError` on
  `out/pytest-a2-run-01`.
- Git showed five tracked state files deleted. Those files were reconstructed
  from exact `HEAD` content; the plan contract differs only by a final newline.
- After cleanup, one Git-tracked committed record remained. The other 592
  ignored run records and associated ignored generated caches are not present in
  Git and could not be recovered.
- Temporary exploratory records, logs, queue/review stores, and provenance were
  removed. No production service, credential, or customer data was involved.

## Pass 1 — per-file review

- `engine/lib/demo_data.py`: argument parsing now precedes the destructive call;
  human and JSON modes share typed flags, help is standard, and integer
  `SystemExit` behavior remains preserved.
- `bin/dashboard.py`: result clearing is centralized; success refreshes update
  newly-created DOM nodes rather than detached references. Error/rate-limit paths
  continue to re-enable retry.
- Regression tests: settings tests make `clear()` fail if help reaches it;
  progress tests pin no-result/error clearing and post-refresh confirmation.

## Pass 2 — cross-file review

- Correctness: dashboard retry continues to call the existing rate-limited API;
  only presentation state changes. The demo dashboard's `--json` invocation is
  still accepted by the new parser.
- Security and reliability: destructive intent now requires a parsed invocation;
  unrecognized flags fail closed. No new path, shell interpolation, secret, or
  network behavior was introduced.
- Deployment: `argparse` is Python standard library; Make, repark, and dashboard
  callers use supported no-argument/`--dry`/`--json` forms.
- Coverage: targeted failure-before/fix-after regressions exist for all three
  findings. Full settings/progress passed (45 tests), adjacent
  dashboard/review/API adversarial suites passed (91 tests), and Python
  compilation plus high-signal Ruff checks passed.

## Residual risk / action plan

1. Rebuild demo history in an isolated state root; do not attempt to fabricate
   the deleted historical records.
2. Resume Feature 5 with release/no-release filters, individual and batch review
   decisions, persistence/restart, and API/CLI parity.
3. Treat the one final-newline normalization in the restored plan contract as a
   recovery-only non-semantic change.
