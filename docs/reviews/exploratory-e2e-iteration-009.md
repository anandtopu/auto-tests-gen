# Exploratory E2E Review — Iteration 009

## Scope

This iteration completed Feature 8, Trace, against the served dashboard and its
real read-only API/CLI paths. It covered JIRA and PR chronology, requirement
matrix joins, generated files, gates and commits, review/release events, CSV,
unknown and malformed keys, corrupt persisted input, and restart persistence.

## Findings

| ID | Severity | Files | Finding | Impact | Fix |
| --- | --- | --- | --- | --- | --- |
| E2E-EXP-016 | P1 | `engine/lib/trace_matrix.py` | Single-agent generate contracts omit per-test `repo`; the matrix therefore dropped repo, gate and commit even when the run had exactly one committed gate. | The audit view broke its central story-to-commit chain and made a committed test look untraceable. | Infer the test repository only when one gate makes ownership unambiguous; never infer across multiple gates or onto a no-test scenario. |
| E2E-EXP-017 | P2 | `engine/lib/trace.py`, `engine/lib/trace_matrix.py`, `bin/dashboard_server.py` | A malformed key raised `SystemExit` through the read join and closed HTTP without a response; valid JSON with wrong record/phase shapes could also crash Trace. | One bad request or persisted record could make the timeline/API unavailable and obscure otherwise valid evidence. | Validate both Trace API keys, make library reads total for invalid keys, and filter malformed record, phase and gate shapes. |

## Reproduction and retest evidence

- Before E2E-EXP-016, the live `PROJ-301-S1` row showed its generated file but
  `—` for repo, gate and commit. The run record itself showed one committed
  `e2e-api-tests-1` gate at `7b49bb4`. After the fix, UI/API/CSV all render that
  exact repo, status and commit, while `PROJ-301-S2/S3` remain `no test yet`
  with blank gate evidence.
- Before E2E-EXP-017, `GET /api/trace?key=../../bad` closed the connection with
  no HTTP response. After the fix, both `/api/trace` and `/api/trace-matrix`
  return 400 and a subsequent authenticated request succeeds. An unknown valid
  key remains the distinct 404 case.
- Focused tests also inserted a JSON array and a partial phase record into an
  isolated run store. The valid `T-9` chronology and key list survive both.
- The PR timeline showed seven pipeline events plus pending review, per-repo
  committed/no-change gates and generated file actions. The JIRA timeline joined
  release, two plan drafts, pending review, latest committed run and critic.
- CSV returned `text/csv`, the declared 16-column header, the PR-path row, the
  covered JIRA row and both uncovered scenario rows.

## Pass 1 — per-file review

- `engine/lib/trace_matrix.py`: record and gate inputs are type-checked. The
  legacy owner fallback derives from a set of gate repositories and activates
  only when that set has one member and a generated test exists. Explicit repo
  metadata still wins; ambiguous fan-out contracts remain unlinked.
- `engine/lib/trace.py`: run records must be mappings with mapping triggers;
  malformed phase entries are skipped, invalid keys return the documented empty
  chain, and existing chronological sorting and presentation contracts remain.
- `bin/dashboard_server.py`: both read-only Trace endpoints reuse plan-key
  validation and convert its controlled exit to an actionable 400. Authentication
  and the valid-unknown 404 contract are unchanged.
- `registry/tests/test_trace_matrix.py`: pins single-gate inference, multi-gate
  non-inference, covered joins, uncovered rows, PR rows, health and CSV.
- `registry/tests/test_trace.py`: pins malformed keys and wrong-shaped records
  while preserving the existing all-source chronology and actor/release fields.
- `registry/tests/test_api_adversarial.py`: the real isolated server pins 400
  responses for both endpoints and proves the connection remains healthy.

## Pass 2 — cross-file review

- Correctness: the real run contract, matrix API, CSV, CLI and served table now
  agree on `PROJ-301-S1 → e2e-api-tests-1 → committed → 7b49bb4`. No-test rows
  deliberately retain empty repository/gate/commit fields.
- Security: malformed keys cannot reach filesystem-derived plan paths or abort
  handler execution. Key errors do not echo file contents or weaken dashboard
  authentication.
- Reliability: invalid JSON, non-mapping records, partial phases and non-mapping
  gates are ignored locally; valid records continue to render. Restarted server
  output matched the pre-restart fixed result.
- Deployment: no dependency, migration, environment, manifest, port, data-store
  or external-service change is required. Legacy and current run contracts are
  both supported.
- Coverage: all focused tests failed on the original behavior. The 198-test
  adjacent set and final 7-test matrix suite pass, as do compilation and Ruff's
  runtime-error rules.

## Seed and cleanup review

Trace consumed only existing synthetic demo run/plan/review data. Mutable queue,
OpenHands and generated dashboard paths were redirected under ignored
`out/exploratory-e2e-iter9`. Adjacent tests temporarily rewrote two tracked
`PROJ-301` fixture files; both were restored exactly to HEAD and verified clean.
The browser tab and exact local server process are closed at iteration end.

## Residual risk

- CI health is absent for the demo rows, so the UI correctly displays `—`; the
  exact-id and file-fallback health joins remain covered by matrix tests.
- More than twelve traced keys are available through API/CLI but the static UI
  key rail shows the twelve most recent. This is an existing bounded display,
  not a correctness blocker for the exercised two-key estate.
- The canonical 1,591-test suite previously exceeded the loop runtime; this
  iteration used the closest 198 checks plus the final matrix, compilation and
  Ruff gates.
- No blocker remains for Feature 8. The next least-covered slice is Feature 9,
  Cost.
