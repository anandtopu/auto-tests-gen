# Exploratory E2E Status

This is the authoritative loop tracker for end-to-end exploratory testing. Each
iteration exercises one coherent feature slice against the served application,
records reproducible findings, fixes verified bugs, and pushes the iteration
before advancing.

Status values: `untested`, `explored-pass`, `bug-open`, `fixed-retested`,
`blocked`, `not-applicable`.

## Feature inventory

| Order | Feature slice | Surfaces | Status | Last evidence | Next focus |
| ---: | --- | --- | --- | --- | --- |
| 1 | Dashboard shell, startup, Overview, navigation | `make serve`, Overview KPIs, attention cards, theme, sidebar, queue header | explored-pass | 2026-08-07: 15/15 primary destinations opened; no console warnings or loader failures; Overview catalog deep link and persisted theme cycle passed; queue badge semantics verified against six failed items | — |
| 2 | Guided PR run | PR form/URL parsing, validation, queue, progress, artifacts | fixed-retested | 2026-08-07: mock `orders-api#201` committed end to end; five invalid-input cases rejected before queueing; stale prior-target results and enqueue/poll races fixed and browser-retested | — |
| 3 | Guided JIRA plan-first run | ticket input, draft plan, approval gate, generation, ticket link | fixed-retested | 2026-08-07: mock `PROJ-301` completed author → approve → generate → committed gate → ticket link; empty/malformed keys and generate-before-approval rejected; stale historical success and Windows queue interpreter failures fixed and browser-retested | — |
| 4 | Intake and work queue | release fetch, inline ticket, plan-only, requeue/remove, drain | fixed-retested | 2026-08-07: known/empty releases, inline validation/dedupe, mode-aware queueing, failed retry, concurrent drain, successful drain and removal exercised; three P2 defects fixed and browser/API-retested | — |
| 5 | Run progress and run review | phase state, failure details, release filters, reviewer decisions | fixed-retested | 2026-08-07: isolated committed/quarantined/refused corpus covered release/review filters, individual and batch decisions, restart persistence, retry/stale cleanup, and API/CLI parity; one in-place filter-coherence P2 fixed and retested | — |
| 6 | Test plans | author, edit, versions, approve/request changes, export/link | fixed-retested | 2026-08-07: isolated served UI covered author queue, approval/generation gate, edit/review lifecycle, signed diff, mock link, four exports and missing/malformed boundaries; plan-key traversal and concurrent stale-edit/decision defects fixed and retested | — |
| 7 | Spec workflow | acceptance criteria, scenarios, waivers, drift and verification | fixed-retested | 2026-08-07: isolated UI/API/CLI covered requirements approval, three structured scenarios, waiver add/remove, expiry/unmatched disclosure, drift, verification and off/warn/strict gates; traversal and three inert UI mutations fixed | — |
| 8 | Trace | PR/JIRA chronology, phase links, empty/unknown keys | fixed-retested | 2026-08-07: served UI/API/CLI/CSV cross-checked PR and JIRA timelines, uncovered scenarios, gate commits, unknown/malformed keys and restart persistence; legacy join and malformed-input defects fixed | — |
| 9 | Cost | measured/simulated spend, caching savings, filters | untested | — | Empty data, simulated-only, mixed measured data |
| 10 | Artifacts | plan/data/tests/diff/code view, coverage report, export/publish | untested | — | Missing/corrupt artifacts and safe rendering |
| 11 | Activity and alerts | transaction filters, degraded log, rule evaluation, acknowledgement | untested | — | Seed success/failure/refusal/corrupt events |
| 12 | Test catalog | search, repo/status filters, mapping, CI health, orphan/quarantine | untested | — | Existing four-row seed plus empty/no-match searches |
| 13 | Repositories | app/test repo CRUD, scopes, curated guidance, contracts/routes | untested | — | Isolated synthetic repo; required-field and dependency checks |
| 14 | Settings and integrations | flags, adapters, credentials metadata, SSO/token modes | untested | — | Safe non-secret fixtures, invalid endpoints, clear/reset behavior |
| 15 | API and CLI parity | dashboard APIs, `bin/qa.py`, hooks/TaskEvent receiver | untested | — | Compare UI outcomes with supported API/CLI paths |
| 16 | Bootstrap/deployment/upgrade | onboarding, manifests, state bundle, migration/rollback checks | untested | — | Fresh isolated estate and compatibility smoke checks |

## Iteration log

### 2026-08-07 — Iteration 1: dashboard shell and Overview

- Environment: local served dashboard, existing synthetic demo estate (576 run
  records, four catalog entries, six page-load queue items).
- Happy paths: application startup, Overview render, sidebar navigation to Guided
  run and back, Overview `tests cataloged` deep link.
- Boundary path: the queue drained from six pending items to zero while the page
  stayed open.
- Negative/health checks: browser console warnings/errors inspected; none found.
- Investigated observation: the header showed zero queued while the sidebar
  showed six. Inspection confirmed all six records were failed items. This is
  intentional: the Run button counts executable queued work while the sidebar
  badge counts queued plus failed work that needs attention. No defect or code
  change was justified.
- Primary navigation: all 15 destinations rendered the expected level-one
  heading with no visible loader-failure state.
- Theme boundary: Auto → Dark persisted across reload, then Dark → Light → Auto
  completed successfully; Auto was restored to avoid leaving test state.
- Result: baseline passed with no confirmed product bug and no source-code
  change. The queue badge copy is a low-risk UX observation, not a defect.
- Validation: `registry/tests/test_ux_getting_started.py` and the live browser
  shell checks passed. Detailed review: `docs/reviews/exploratory-e2e-iteration-001.md`.
- Next slice: Guided PR run, including missing/invalid input and a safe synthetic
  queue seed before any full pipeline execution.

### 2026-08-07 — Iteration 2: Guided PR run

- Seed data: existing deterministic mock SCM fixture `orders-api#201`; no new
  seed files, credentials, PII, or production services were used.
- Happy path: the Guided run UI queued and drained the real mock pipeline,
  produced run `1786125125-15894`, displayed the generated spec and coverage
  link, and ended `committed` with team review pending.
- Negative and boundary paths: empty form, repo without PR number, unregistered
  repo, nonnumeric PR, multiple optional ticket keys, and an unregistered GitHub
  PR URL all produced actionable errors before invalid work was queued.
- Finding `E2E-EXP-002` (P2): after a successful PR, editing the form and
  submitting an invalid second target left the first target's committed ladder
  and generated file visible beside the new inputs.
- Fix: target edits now clear the keyed ladder/artifacts, cancel future polling,
  discard late responses by revision/key/mode, and briefly lock PR inputs while
  enqueue/start requests are in flight to close the pre-poll race. Both direct
  and plan-first PR intake share the submission lock.
- Regression evidence: the new focused test failed before the fix and passed
  afterward; the original browser reproduction and an immediate-edit race were
  retested with zero console warnings/errors.
- Broad verification: 1,574 pytest/coverage checks passed at 70.19%; all adapter
  conformance, adversarial, bootstrap, entrypoint, replay, context, discovery,
  retrieval, reviewer, and scorecard checks passed through Git Bash. The default
  WSL launcher timed out before those shell stages, so the same scripts were run
  directly with the repository-supported Git Bash runtime.
- Review: `docs/reviews/exploratory-e2e-iteration-002.md`.
- Next slice: Guided JIRA plan-first run.

### 2026-08-07 — Iteration 3: Guided JIRA plan-first run

- Seed data: existing deterministic mock JIRA fixture `PROJ-301`; no new seed
  files, credentials, PII, or production services were used.
- Negative and boundary paths: every action rejected an empty ticket; malformed
  `bad key!` failed server validation without queueing; generation from a draft
  plan was blocked with the required approval action.
- Happy path: the served UI authored the draft, recorded dashboard approval,
  generated one API spec through the real mock pipeline, committed its quality
  gate, exposed the generated artifact/coverage link, and linked the plan/tests
  summary to the mock ticket.
- Finding `E2E-EXP-003` (P2): after a plan was re-authored and
  `generated_run` cleared, the wizard hid old tests/gates but still rendered an
  older run id, `Last run: committed`, and agent-review result. The status
  aggregator now treats all generated-run evidence as one correlation and
  fails closed for missing or dangling references.
- Finding `E2E-EXP-004` (P2): background generation launched by the Windows
  dashboard inherited a native PATH that resolved `python3` to an unexecutable
  Microsoft Store WindowsApps shim, failing the supported flow with exit 127.
  Queue execution now crosses the existing normalized Git Bash command boundary
  and explicitly prioritizes the running project interpreter directory.
- Regression evidence: both focused tests failed before their fixes. The queue
  and wizard suites then passed (19 tests); 47 adjacent plan-first, dashboard,
  and environment compatibility tests also passed. The original live browser
  reproduction completed with run `1786127890-16836` and a committed gate.
- Review: `docs/reviews/exploratory-e2e-iteration-003.md`.
- Next slice: Intake and work queue lifecycle.

### 2026-08-07 — Iteration 4: Intake and work queue

- Seed data: existing mock release ticket `PROJ-301` and PR
  `PR-orders-api-201`, plus isolated synthetic inline ticket
  `EXPLORE-QUEUE-1` and missing ticket `NO-SUCH-1`. Queue/review stores were
  redirected under ignored `out/exploratory-e2e`; all temporary data was
  credential-free, PII-free, and removed after the run.
- Happy and boundary paths: known and unknown release fetch, empty/malformed and
  valid inline intake, duplicate suppression, plan-only/full-run eligibility,
  remove/requeue, failed retry, simultaneous drain requests, and a successful
  queued → running → done PR drain were exercised through the served UI and API.
- Finding `E2E-EXP-005` (P2): release fetch invoked the tracker with an
  unnormalised Windows runtime and converted exit 127 into a plausible empty
  release. The adapter now uses the shared Git Bash/runtime boundary and returns
  an actionable 502 for process, JSON, or response-shape failures.
- Finding `E2E-EXP-006` (P2): removing or requeueing an item refreshed the queue
  table but left fetched release actions stale. Queue mutations now also refresh
  an already-open fetched-results card.
- Finding `E2E-EXP-007` (P2): two simultaneous run requests both passed a
  `locked()` check and returned `200 started`. The request thread now acquires
  the singleton runner lock atomically, releases it in every worker/launch exit,
  and returns 409 to the competing request.
- Regression evidence: four focused tests passed; the live reproductions changed
  from empty release to `PROJ-301`, stale `Queued` to restored actions, and
  `200/200` concurrent drains to `200/409`. The success seed completed at exit 0
  and rendered `done` before removal.
- Review: `docs/reviews/exploratory-e2e-iteration-004.md`.
- Next slice: Run progress and run review.

### 2026-08-07 — Iteration 5: Run progress partial / demo-data safety incident

- Progress scenarios exercised before the stop: committed and quarantined run
  ladders, exit-code meaning and log tail, explainability evidence, unknown key,
  malformed key, and retry queueing. Temporary quarantined/reviewer-refused
  fixtures and isolated review/queue stores were synthetic and PII-free.
- Finding `E2E-EXP-008` (P2): after viewing a failed run, an unknown or malformed
  key retained the prior Retry action and could retain prior failure evidence.
  No-result and load-error paths now clear the ladder, failure panel, retry bar,
  explanation, and stale source badge.
- Finding `E2E-EXP-009` (P2): successful retry queueing immediately re-rendered
  away its confirmation and exposed a new enabled Retry button. The refreshed
  nodes now retain the confirmation and disable duplicate submission.
- Incident `E2E-EXP-010` (P1): invoking `engine/lib/demo_data.py --help` during
  seed discovery did not parse help; it entered the destructive default clear
  and stopped only after reaching a locked directory. The ignored run corpus
  fell from 593 committed records to the one Git-tracked baseline record (592
  ignored run records lost), with generated plan/review/guidance caches removed
  as well. Git-tracked state was reconstructed from HEAD; ignored runtime
  evidence cannot be recovered from Git.
- Safety fix: the CLI now parses all flags with `argparse` before calling
  `clear()`. `--help` exits 0 without touching state, unknown flags fail closed,
  and the existing human/JSON modes retain their prior output/exit contracts.
- Validation: 45 settings/progress tests and 91 adjacent review/dashboard/API
  adversarial tests passed; Python compilation and high-signal Ruff checks also
  passed. The real `demo_data.py --help` command returned usage text at exit 0.
- The Run progress/review slice is `blocked`, not complete: release filters,
  team-review transitions, and batch approval still need a rebuilt isolated
  corpus. Review: `docs/reviews/exploratory-e2e-iteration-005.md`.

### 2026-08-07 — Iteration 6: Run progress and team review completion

- Seed data: four deterministic temporary run records represented committed,
  pending, quarantined, and reviewer-refused outcomes. Review, queue, and
  testcase-provenance stores were redirected under ignored
  out/exploratory-e2e-iter6. The corpus was synthetic, credential-free,
  PII-free, isolated from production, and removed after validation.
- Review coverage: all, 2026.08, 2026.09, and no-release filtering; awaiting,
  approved, and changes-requested filtering; individual approval; confirmed
  two-key batch approval; and persisted decisions after a server restart.
- Progress coverage: the quarantined ladder, exit-code explanation, missing-log
  disclosure, one retry queue record, retained disabled confirmation, and stale
  failure/action cleanup for unknown and malformed keys were browser-retested.
- Finding `E2E-EXP-011` (P2): individual approval changed the visible chip but
  left the row data-review value pending. The approved row stayed under
  awaiting review and was absent under approved until reload.
- Fix: the in-place handler now updates the row dataset and reapplies active
  filters as one successful transition. The browser result changed from 2/5
  awaiting and 1/5 approved to 1/5 awaiting and 2/5 approved immediately.
- API/CLI parity: unknown key, invalid state, missing changes-request note, and
  malformed JSON returned 409/409/409/400; a CLI changes-request appeared in
  the served UI, and a valid API approval appeared in bin/qa.py reviews.
- Validation: the focused regression failed before the fix and passed after it;
  113 adjacent UI/progress/review/API adversarial tests passed. Python
  compilation and the high-signal Ruff runtime-error subset passed. Full Ruff
  still reports 35 pre-existing style/baseline findings in the two legacy files.
- Multi-pass review found no additional actionable correctness, security,
  reliability, deployment, or coverage defect. Review:
  `docs/reviews/exploratory-e2e-iteration-006.md`.
- Next slice: Test plans.

### 2026-08-07 — Iteration 7: Test plans

- Seed data: deterministic local plans `EXP-PLAN-1` and `EXP-AUTHOR-2` used
  plan lifecycle, markdown, and queue stores redirected under ignored
  `out/exploratory-e2e-iter7`. The corpus was synthetic, credential-free,
  PII-free, mock-only, and never targeted production or customer services.
- Happy paths: the served UI opened and edited a plan, queued plan authoring,
  moved through review/approval, queued generation only after approval, linked
  a PDF through the mock tracker, revoked approval after a later edit, and
  rendered the exact diff from the signed baseline. Markdown, HTML, DOCX, and
  PDF exports all returned the correct media type and non-empty content.
- Negative and boundary paths: generation before approval, missing
  request-changes note, unknown export key, a traversal-shaped plan key,
  malformed plan text, an unversioned overwrite, and two-tab stale edits and
  lifecycle decisions were exercised. Missing/invalid inputs returned
  actionable 4xx responses without writing outside the configured plan store.
- Finding `E2E-EXP-012` (P1): `POST /api/plans/save` accepted
  `../escaped-plan`, wrote markdown outside `AIQE_TESTPLAN_DIR`, and persisted
  the attacker-controlled key in lifecycle state. Plan keys are now validated
  centrally before every derived path or state mutation; legacy invalid entries
  are ignored by summaries so they cannot break dashboard generation.
- Finding `E2E-EXP-013` (P2): two reviewers loaded the same revision, reviewer B
  saved, and reviewer A's stale save still returned 200 while erasing B's edit.
  The editor and API now use a SHA-256 revision covering plan bytes plus
  lifecycle state, read atomically under the plan-state lock. Existing-plan web
  mutations require the token and return 409 for stale or missing revisions.
- Retest evidence: the browser result changed from a successful stale overwrite
  with `Lost_B_edit=true` to `stale plan revision` while reviewer B's text
  remained durable. Traversal changed from HTTP 200 plus an escaped file to 409
  with no file. Approval/generation, post-approval diff, mock linking, exports,
  request-changes note validation, and author queueing remained functional.
- Validation: 58 plan-version/API-adversarial tests and 87 adjacent plan,
  export, PR-plan, similarity, reuse, and dashboard UI tests passed. Detailed
  compilation and high-signal Ruff checks passed. The full 1,591-test canonical
  suite exceeded the 10-minute iteration bound without producing a result;
  targeted and adjacent gates remained green. Detailed review:
  `docs/reviews/exploratory-e2e-iteration-007.md`.
- Next slice: Spec workflow.

### 2026-08-07 — Iteration 8: Spec workflow

- Seed data: the tracked synthetic `PROJ-301` structured requirements/spec was
  copied into mutable stores under ignored `out/exploratory-e2e-iter8`. Two
  one-day waivers represented a matching scenario and a typoed unmatched
  scenario. The seed was deterministic, credential-free, PII-free, mock-only,
  and never targeted production or customer services.
- Happy paths: the served UI loaded two EARS requirements and three structured
  scenarios, signed the requirements hash, created and removed a waiver, and
  rendered the matching waiver as `1d left` while the typoed waiver read
  `MATCHES NOTHING`. The real workflow and drift CLIs reported the six-state
  board and a clean surface comparison. Verification's no-catalog-mapping path
  returned its documented non-zero result instead of claiming tests ran.
- Negative and boundary paths: missing waiver owner returned actionable 422;
  unmatched waiver save returned an immediate warning; a one-day expiry was
  accepted; expired/live and strict/warn/off gate behavior, stale drift, and
  unverifiable verification remained covered by the adjacent suites.
- Finding `E2E-EXP-014` (P1): an authenticated waiver save accepted
  `../escaped-waiver` and wrote `waivers.yaml` outside `AIQE_SPEC_DIR`.
  Structured-spec keys are now validated centrally before every derived path;
  invalid read paths stay total, and waiver GET/save/remove return safe 4xx
  responses. The real HTTP reproduction changed from 200 plus an escaped file
  to 400 with no file.
- Finding `E2E-EXP-015` (P2): Add waiver, Remove waiver, and Approve requirements
  passed payloads directly as `fetch` options, silently issuing GET requests to
  POST-only endpoints. Add waiver also supplied no owner outside SSO mode. All
  three controls now send JSON POSTs; the form captures an owner while trusted
  SSO identity continues to override it. Live browser retests created, removed,
  and approved successfully.
- Validation: both focused regressions failed before their fixes. Afterward,
  204 Spec workflow, gate, drift, verification, UI, API-adversarial, event-log,
  and requirements-gate tests passed; compilation and high-signal Ruff checks
  passed. Detailed review:
  `docs/reviews/exploratory-e2e-iteration-008.md`.
- Next slice: Trace chronology and empty/unknown-key behavior.

### 2026-08-07 — Iteration 9: Trace

- Seed data: the existing local demo run history provided synthetic JIRA
  `PROJ-301` and PR `PR-orders-api-201` chains. Only queue, OpenHands and
  generated-AGENTS paths were redirected under ignored
  `out/exploratory-e2e-iter9`; Trace itself was read-only. No credential,
  customer record or production service was used.
- Happy paths: the served UI rendered both chronological timelines with run,
  plan, review, release, critic, generated-file and per-gate evidence. The
  matrix and CSV showed the PR-path test, one covered JIRA scenario and two
  loud `no test yet` scenarios. CLI/API parity, key listing, unknown-key 404,
  CSV media type/content, and identical results after server restarts passed.
- Finding `E2E-EXP-016` (P1): the real single-agent JIRA generate contract did
  not carry per-test `repo` metadata, so the matrix showed a generated spec but
  blank repo, gate and commit columns even though the same run committed one
  gate. When exactly one gate makes ownership unambiguous, Trace now uses that
  repository; multi-gate legacy rows stay unknown rather than guessing.
- Finding `E2E-EXP-017` (P2): a traversal-shaped Trace key escaped the module's
  claimed total-read contract because `plan_state` raised `SystemExit`; the
  HTTP handler closed the connection without a response. Well-formed but
  wrong-shaped run records could also crash timeline joins. Both Trace APIs now
  return 400 for malformed keys, and library joins skip invalid record/phase
  shapes while preserving valid evidence.
- Review finding fixed before commit: the first inference patch also attached
  the sole successful gate to scenarios with no test. Inference is now scoped
  to actual generated tests; the two uncovered scenarios remain blank and
  conspicuous in UI, CLI and CSV, with regression coverage.
- Validation: all three focused regressions failed before implementation and
  passed after it. A 198-test adjacent Trace/API/CLI/UI/event/reuse/spec suite,
  the final 7-test matrix suite, Python compilation and high-signal Ruff checks
  passed. Detailed review:
  `docs/reviews/exploratory-e2e-iteration-009.md`.
- Next slice: Cost reporting, measured/simulated spend and cache savings.
