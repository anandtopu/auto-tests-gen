# Exploratory E2E Review — Iteration 007

## Scope

This iteration completed Feature 6, Test plans, against a served dashboard with
isolated synthetic plan, lifecycle, and queue state. It covered author queueing,
editing, concurrent reviewers, lifecycle decisions, approval gating, signed
version diffs, mock linking, and every supported export format.

## Findings

| ID | Severity | Files | Finding | Impact | Fix |
| --- | --- | --- | --- | --- | --- |
| E2E-EXP-012 | P1 | `engine/lib/plan_state.py`, `bin/dashboard_server.py` | The plan-save POST accepted `../escaped-plan`; the derived markdown path escaped `AIQE_TESTPLAN_DIR` and the invalid key entered lifecycle state. | An authenticated dashboard caller could overwrite a writable markdown path outside the configured plan directory; the corrupt entry could later break dashboard rendering. | Validate plan keys centrally before every path/state mutation, return 4xx for invalid keys, and omit legacy invalid entries from summaries. |
| E2E-EXP-013 | P2 | `engine/lib/plan_state.py`, `bin/dashboard_server.py`, `bin/dashboard.py` | Existing-plan saves and review decisions had no concurrency token. A stale tab returned success and silently replaced a newer reviewer's edit. | Human review evidence and approval state could be lost or changed without the stale reviewer seeing a conflict. | Return one locked text/lifecycle revision, require it for existing-plan web mutations, compare it inside the mutation lock, and surface an actionable 409 to stale editors. |

## Reproduction and retest evidence

- Before the fix, two API reads loaded identical text, reviewer B saved a
  boundary addition, then reviewer A saved the stale copy. Both responses were
  200 and the final text omitted B's work (`Lost_B_edit=true`).
- After the fix, the same two-tab served-UI flow rejected reviewer A with
  `stale plan revision — another reviewer changed this plan`; reviewer B's text
  remained the stored plan. A stale lifecycle decision is rejected the same
  way because the revision includes status, update time, and history length.
- Before the fix, saving `../escaped-plan` returned 200 and created
  `state/escaped-plan.md`. After the fix it returns 409 and no escaped file is
  created. The focused tests failed before implementation and pass afterward.
- Draft generation was refused; approval enabled queueing; mock JIRA linking
  recorded its attachment; an approved-plan edit reset status to draft and
  displayed the unified `Changed since last approval` delta.
- Markdown, HTML, DOCX, and PDF exports returned 200 with their expected media
  types and non-empty payloads. An unknown key returned 404 and listed the
  available synthetic plan instead of an opaque failure.

## Pass 1 — per-file review

- `engine/lib/plan_state.py`: key validation is ASCII, bounded, and applied at
  every filesystem/state entry point. The optimistic revision covers text and
  lifecycle metadata. Reads and compare/mutate operations use the existing
  cross-platform lock; approval snapshotting now occurs under that same lock.
  Direct CLI/library callers remain compatible, while the web layer enforces a
  token for existing plans. Non-string markdown now fails validation instead of
  raising an unhandled attribute error.
- `bin/dashboard_server.py`: plan reads return text, state, and revision from one
  locked snapshot. Invalid keys return 400/409 rather than reaching pathlib;
  existing-plan save/status requests without a revision fail closed. No auth,
  external adapter, or production service boundary was weakened.
- `bin/dashboard.py`: the editor stores the revision returned by each open and
  includes it on save and lifecycle actions. A successful action reopens the
  plan and refreshes the token; a stale failure leaves the user's text visible
  and reports that a reload is required.
- `registry/tests/test_plan_versions.py`: focused unit coverage pins traversal
  rejection, stale text and status decisions, malformed text, and recovery from
  a legacy invalid state entry.
- `registry/tests/test_api_adversarial.py`: the real isolated server pins path
  containment, revision issuance, missing/stale token conflicts, preservation
  of the winning edit, and preservation of the winning lifecycle decision.
- `docs/user-guide.md` and `docs/exploratory-e2e-status.md`: document the
  concurrent-review contract and record completion only after live retesting.

## Pass 2 — cross-file review

- Correctness: plan text, status, and revision are read consistently. Revision
  comparison and mutation share the same lock, closing check/write and
  approval/edit races. Successful UI actions refresh the token before another
  mutation; stale actions cannot become last-write-wins.
- Security: plan keys can no longer introduce separators, absolute paths,
  trailing-dot aliases, or unbounded filenames. Invalid legacy keys are not
  dereferenced by summary/trace generation. HMAC-safe digest comparison avoids
  token timing differences, although the revision is a concurrency token, not
  an authorization secret.
- Reliability: lifecycle state still uses atomic JSON replacement and bounded
  version snapshots. Approval snapshots now correspond to the exact locked
  revision. Old invalid state remains recoverable in the source JSON but cannot
  take down the dashboard.
- Deployment: no dependency, database, migration, environment, manifest, port,
  or external-service change is required. Existing dashboard clients must use
  the revision returned by `/api/plans/one`; the bundled UI does so.
- Coverage: focused tests fail against the old behavior and pass with the fix;
  adjacent plan workflow, export, PR-plan, similarity, reuse, and UI suites pass.
  Python compilation and Ruff E9/F63/F7/F82 checks also pass. The full canonical
  1,591-test run was attempted separately but exceeded the 10-minute bound with
  buffered output and no final result; it is not counted as passing evidence.

## Seed and cleanup review

All seed records lived in ignored `out/exploratory-e2e-iter7` stores and used
synthetic keys/text. Browser tabs were finalized and the exact local server
process was stopped. No shared plan/review state, credentials, real JIRA issue,
customer record, or production endpoint was used.

## Residual risk

- The stale-tab behavior was browser-retested live; CI pins it at the API and
  plan-state layers rather than with a full browser test.
- Legacy invalid entries are deliberately ignored, not automatically deleted,
  so operators can recover/inspect state without a destructive migration.
- Link delivery used the deterministic mock tracker. Real JIRA attachment
  availability remains an integration concern, not a blocker for this slice.
- The full canonical suite did not finish inside the bounded iteration; the 145
  focused/adjacent checks cover every changed surface, while broader completion
  remains a non-blocking verification risk for a longer CI runner.
- No blocker remains for Feature 6. The next least-covered slice is Feature 7,
  Spec workflow.
