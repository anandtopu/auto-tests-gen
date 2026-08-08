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
| 9 | Cost | measured/simulated spend, caching savings, filters | fixed-retested | 2026-08-07: deterministic mixed-basis corpus covered reported, estimated, local, simulated and unpriced spend, turn/cache calibration, reuse evidence, filters and corrupt-state resilience; four disclosure/reliability defects fixed | — |
| 10 | Artifacts | plan/data/tests/diff/code view, coverage report, export/publish | fixed-retested | E2E-EXP-022 | Browser + CLI/API: JIRA/PR artifacts, scenarios/data/generated code/raw diff, coverage download, four export formats, mock publish/attach, missing/unsafe diff evidence |
| 11 | Activity and alerts | transaction filters/CSV, degraded log, rule evaluation, firing/resolution lifecycle | fixed-retested | E2E-EXP-023–027 | Browser + API/CLI: success/refusal/failure/corrupt events, filters, formula-safe CSV, unevaluable/firing/disabled/resolved states, mock delivery, rule save/test, restart persistence |
| 12 | Test catalog | search, repo/status filters, mapping, CI health, orphan/quarantine | fixed-retested | E2E-EXP-028–030 | Browser + CLI: four-row isolated catalog, valid/empty/orphan mappings, synthetic mixed CI health, flaky quarantine lifecycle, concurrent decisions and empty/no-match filters |
| 13 | Repositories | app/test repo CRUD, scopes, curated guidance, contracts/routes | fixed-retested | E2E-EXP-031–034 | Browser + API: isolated app/test CRUD, service dependencies, generated scope/covers, contract/routes, notes/curated guidance, malformed bodies, removal guards and restart persistence |
| 14 | Settings and integrations | flags, adapters, credentials metadata, SSO/token modes | fixed-retested | E2E-EXP-035–038 | Browser + API: isolated save/reload/removal, write-only secrets, safe connection checks, token/SSO coverage, malformed-body and atomic-write resilience |
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

### 2026-08-07 — Iteration 10: Cost

- Seed data: one ignored numeric run record added a deterministic synthetic mix
  of provider-reported, list-price-estimated, local, simulated and unpriced
  phases plus two artifact reuses (800 reported and 200 estimated avoided
  tokens). A second ignored record deliberately used `phases: null`. Both were
  PII-free, credential-free, local-only and removed after retest.
- Happy paths: CLI, authenticated API and the served Cost view agreed on the
  $2.2000 priced subtotal, 92% simulated share, five provider bases, local/cloud
  token split, workflow/key/phase rollups, cache hit rate, turn p50/p95 and
  suggested ceilings. A one-day filter returned 200 and the view survived
  regeneration and server restart.
- Finding `E2E-EXP-018` (P1): the API identified one unpriced cloud call, but
  the browser still presented `$2.2000` as an unqualified total. The Cost badge
  and summary now say `incomplete`, name the unpriced provider and state that
  those calls are excluded.
- Finding `E2E-EXP-019` (P2): the report contained two artifact reuses and 1,000
  avoided tokens, but the Cost view showed only phase-cache hits. It now renders
  both reuse mechanisms separately and retains reported/estimated token bases
  without inventing dollar savings.
- Finding `E2E-EXP-020` (P1): one well-formed JSON run with a wrong-shaped
  `phases` or spend field closed the Cost API connection and could abort static
  dashboard generation; the same `phases: null` row also escaped Trace's total
  reader. All three readers now validate record, timestamp, trigger, phase and
  spend shapes, skip only corrupt evidence, and preserve valid runs.
- Finding `E2E-EXP-021` (P2): `days=-1` returned a misleading empty report and
  a sufficiently large integer closed the connection. API/library/CLI windows
  are now bounded to 1–36,500 days, invalid input returns actionable 400/exit 2,
  and the next request stays healthy.
- Review finding fixed before commit: malformed numeric or label values inside
  an otherwise mapping-shaped spend row could still crash aggregation. Spend
  fields are now normalized only after finite, non-negative and type checks;
  a focused regression failed before this hardening and passed afterward.
- Validation: the three initial focused regressions failed before the fix; the
  spend-field review regression also failed before hardening. Browser/API/CLI
  reproductions then passed, including dashboard regeneration with corrupt
  state present. The final 270-test adjacent cost/budget/cache/provider/artifact/
  UI/API/Trace suite, compilation and high-signal Ruff checks passed. Detailed
  review: `docs/reviews/exploratory-e2e-iteration-010.md`.
- Next slice: Artifacts, including missing/corrupt bundles and safe rendering.

### 2026-08-07 — Iteration 11: Artifacts

- Seed data: the existing synthetic PROJ-301 JIRA run and
  PR-orders-api-201 PR run exercised the complete artifact layout. One ignored
  numeric PR record referenced C:\Windows\win.ini as a hostile persisted diff
  path. It was deterministic, credential-free, PII-free, local-only, and was
  removed after retest.
- Happy paths: the served Artifacts view rendered the plan, three scenarios,
  three test-data files, generated-test metadata, validation, open questions,
  clean generated code, catalog sidecar, raw commit diff and PR coverage
  report. CLI parity passed. Markdown, HTML, DOCX and PDF downloads returned
  their correct media types and non-empty content; mock Confluence publish and
  JIRA attach both succeeded.
- Finding E2E-EXP-022 (P1): a persisted gate diff was joined directly to the
  checkout root. The real artifact CLI printed C:\Windows\win.ini, proving
  arbitrary readable local-file disclosure; the dashboard used the same open
  path. A shared resolver now accepts only relative .diff files confined below
  reports/runs, including after symlink resolution. Unsafe paths are refused
  visibly and missing archives are reported rather than silently disappearing.
- Retest evidence: the same CLI reproduction changed from printing win.ini to
  unsafe diff path refused, and the served UI displayed Unsafe diff refused
  without file contents. The valid PROJ-301 archived diff still rendered as
  structured generated code plus its raw-diff toggle.
- Review finding: no further actionable issue was found. The path boundary is
  shared by CLI and dashboard, rejects absolute/traversal/non-diff input, and
  leaves valid historical evidence backward compatible.
- Validation: 30 focused tests passed, followed by 187 artifact/export/UI/API/
  storage/reuse/task-bundle compatibility tests. Python compilation and
  high-signal Ruff checks passed. Detailed review:
  docs/reviews/exploratory-e2e-iteration-011.md.
- Next slice: Activity and alerts, including malformed audit rows, rule
  evaluation, notification isolation and restart behavior.

### 2026-08-07 — Iteration 12: Activity and alerts

- Seed data: an ignored isolated event directory contained three synthetic
  success/refusal/failure transactions, a spreadsheet-formula actor, one JSON
  scalar and one torn line. An isolated rules file contained a threshold-one
  gate refusal and a disabled rule. All data was deterministic, PII-free,
  credential-free, mock-only and removed after retest.
- Happy paths: the served Activity view rendered three valid rows, exact kind
  filtering, outcomes, durations and run correlation while reporting two
  unreadable lines. CSV export returned the same evidence and prefixed the
  formula-shaped actor. Alerts rendered unevaluable, firing, disabled and
  resolved states; mock delivery produced notify.sent, and CLI output matched
  the browser/API across a server restart.
- Finding E2E-EXP-023 (P1): Overview, Alerts GET and qa.py alerts evaluated with
  notification disabled but still persisted firing state. Merely observing a
  rule consumed its transition, so the later maintenance tick never delivered
  it. Read-only surfaces now evaluate with commit disabled; the scheduled tick
  alone records state and sends.
- Finding E2E-EXP-024 (P1): syntactically valid wrong-shaped event rows were
  returned as transactions and crashed downstream CLI/UI consumers. Malformed
  rule, match, recipient or state values likewise escaped normalization.
  Readers now count non-event JSON as corrupt, and rule normalization degrades
  each bad field with an explicit problem rather than aborting its neighbors.
- Finding E2E-EXP-025 (P1): Activity disclosed corrupt lines, but the alert
  evaluator still called the same incomplete window firing or ok. Any corrupt
  line now makes enabled rules unevaluable with the lost-line count.
- Finding E2E-EXP-026 (P2): Save rules and Test sent GET requests to POST-only
  endpoints and always returned not found. Both controls now send JSON POSTs;
  the live browser saved three rules with validation feedback and delivered a
  mock channel test. Non-object JSON returns 400 and the next request remains
  healthy.
- Finding E2E-EXP-027 (P1): saving rules discarded firing, notification and
  cooldown state. An unchanged save could suppress resolution and re-notify a
  still-firing rule. Server-side edits now preserve normalized lifecycle state
  by unique rule id, and the merge plus atomic replace share one lock so a
  maintenance/UI race cannot reintroduce the loss.
- Review reconciliation: the original matrix said acknowledgement, but no
  manual acknowledgement contract exists in the architecture or user guide.
  The documented lifecycle is firing, cooldown and automatic resolution, so
  the matrix now names and tests that actual feature rather than inventing one.
- Validation: the initial focused regressions failed in six cases plus the
  corrupt-window case. After fixes, 90 focused Activity/Alerts/live-API tests
  and 233 adjacent observability, UI, notification, isolation and regression
  checks passed. Compilation and high-signal Ruff checks passed. Detailed
  review: docs/reviews/exploratory-e2e-iteration-012.md.
- Next slice: Test catalog search, filters, mapping, CI health, orphan and
  quarantine lifecycle.

### 2026-08-07 — Iteration 13: Test catalog

- Seed data: the four tracked catalog rows were copied into ignored
  `out/exploratory-e2e-iter13` with an isolated registry, generated AGENTS
  path, query index and CI-health file. One synthetic Jenkins result contained
  two passes, one failure and one unmatched case. It was deterministic,
  PII-free, credential-free, local-only and never contacted a production
  service.
- Happy and boundary paths: the served Catalog rendered all four rows; repo,
  status, title/file/app search and a no-match search produced counts of 1/4,
  1/4, 1/4 and 0/4. Valid mapping to `catalog-api` and restoration to
  ORPHAN regenerated isolated coverage. CI ingest matched three cases, left one
  unmatched, calculated 67% pass/FLAKY after three runs, and CLI/UI agreed.
  Quarantine, note display, unquarantine behavior and two simultaneous
  quarantine writers were exercised through supported entry points.
- Finding `E2E-EXP-028` (P2): `qa.py map --repos ''` accepted an
  empty repository list and wrote `status=confirmed` with confidence 1.0.
  Overview counted the row as mapped while Catalog displayed app repo `—`.
  Empty or delimiter-only decisions now fail with guidance to use the explicit
  ORPHAN decision, before mutating the row.
- Finding `E2E-EXP-029` (P1): catalog JSONL mutation opened the durable
  shard directly. Fault injection after serializing the first row left only
  that partial new row, losing the prior catalog; simultaneous read-before-lock
  writers could also overwrite one another. Serialization now completes before
  disk I/O, same-volume replacement is atomic, and mapping/review/quarantine
  hold the existing cross-process lock across the full read-modify-replace
  transaction. Two simultaneous real CLI writes preserved both notes.
- Finding `E2E-EXP-030` (P2): quarantine existed in catalog state and the
  flaky CLI, but the Catalog UI rendered only the mapping-status chip. The
  served row now shows an escaped quarantine badge and human note next to
  mapping status; the original browser reproduction changed from invisible to
  `⚠ quarantined` plus the synthetic note.
- Broad-check findings: a ticket-discovery test still asserted the retired
  direct subprocess argv instead of the supported Git-Bash normalization
  boundary; it now asserts the semantic pipeline script/arguments. A
  multi-agent integration test shared phase-cache state with unrelated pipeline
  tests and intermittently read the authored one-scenario plan; the adversary
  lifecycle tests now disable cache so they measure fresh arbitration.
- Validation: 18 focused catalog/quarantine checks, 97 adjacent catalog,
  health, integrity, portability and API-adversarial checks, 12 work-queue
  checks, and all 35 multi-agent checks passed. Two full 1,628-test runs each
  passed 1,627 tests and exposed one different pre-existing suite-isolation
  defect; both failing contracts pass after their focused fixes. Compilation,
  high-signal Ruff and whitespace checks passed. Detailed review:
  `docs/reviews/exploratory-e2e-iteration-013.md`.
- Next slice: Repositories, including app/test repository CRUD, scopes, curated
  guidance, contracts/routes and dependency checks.

### 2026-08-07 — Iteration 14: Repositories

- Seed data: an ignored `out/exploratory-e2e-iter14` estate copied only the
  registry and catalog mappings, then added synthetic `zz-explore-api`,
  `zz-explore-ui` and `zz-explore-e2e` entries. Registry, catalog, notes,
  curated/generated guidance, AGENTS, skills, sync cache and database paths
  were redirected. No production adapter, real credential or customer data
  was used.
- Happy and boundary paths: the served Repositories UI created service and UI
  apps with a dependency link, displayed contract and route metadata, created
  a Playwright API test repo scoped to both apps, regenerated `covers`, edited
  domains, saved team and curated guidance, rejected an unknown service and
  unknown scope, refused removal while covered, and retained all metadata and
  guidance across a server restart.
- Finding `E2E-EXP-031` (P2): every repository mutation assumed parsed JSON was
  an object. Arrays, null, numbers and strings raised outside the handler and
  reset the connection. All eight endpoints now return a stable 400 and the
  next request remains healthy.
- Finding `E2E-EXP-032` (P1): repository removal and guidance generation used
  Python truthiness for `force`; the JSON string `"false"` bypassed the app
  dependency guard and deleted a covered repository. Both operations now use
  the platform's fail-safe JSON flag resolver; the real HTTP reproduction
  changed from 200/removal to 400/preservation.
- Finding `E2E-EXP-033` (P1): team guidance remained hardcoded below the source
  checkout while all other durable estate paths followed `AIQE_STATE_DIR`.
  A container or isolated test could therefore write the wrong estate and
  regenerate AGENTS from split state. Notes now resolve through
  `app_paths.knowledge_dir`; the browser confirmed the isolated path and
  restart persistence.
- Finding `E2E-EXP-034` (P2): a missing or misspelled removal `section`
  silently selected the app-repository remover. The API now requires exactly
  `app` or `test`, preventing a malformed request from reaching either
  destructive path.
- Review finding fixed before commit: the adversarial server fixture initially
  copied the complete catalog directory. It now seeds JSONL data only, keeping
  code, generated caches and configuration out of mutable test state.
- Validation: all 34 initial focused cases failed before their fixes. The final
  adversarial/state-path suite passed 100 tests, and the broad repository,
  guidance, routing, catalog-path, UI and API compatibility run passed 215.
  Detailed review: `docs/reviews/exploratory-e2e-iteration-014.md`.
- Next slice: Settings and integrations, including flags, adapter metadata,
  authentication modes and safe reset behavior.

### 2026-08-07 — Iteration 15: Settings and integrations

- Seed data: an ignored `out/exploratory-e2e-iter15/settings.env` stored one
  synthetic JIRA URL and one explicitly non-secret SMTP placeholder. The
  localhost server ran with mock adapters, a test bearer token and every known
  external credential removed from its environment; no production probe ran.
- Happy and boundary paths: the served Settings UI rendered every integration
  section, saved and reloaded a non-secret value, retained a write-only secret
  without returning it through the API, explicitly cleared the JIRA value,
  and ran all 11 connection checks as safely not configured. Token access
  returned 200 while an unauthenticated settings read returned 401; existing
  SSO fail-closed and trusted-header paths passed their compatibility suite.
- Finding `E2E-EXP-035` (P1): clearing a `.env` setting left the previous value
  in the long-lived dashboard process. A later connection check or adapter
  entry point could therefore keep using a removed credential, URL or proxy.
  Refresh now removes prior file-owned values and only removes/updates standard
  proxy aliases that the settings loader itself supplied.
- Finding `E2E-EXP-036` (P1): Settings rewrote `.env` in place. Interruption or
  disk failure could truncate the estate's full integration configuration.
  Save now writes a same-directory temporary file and uses the shared retried
  atomic replacement helper; fault injection preserved the complete original.
- Finding `E2E-EXP-037` (P2): arrays, null, numbers and strings sent to either
  Settings POST endpoint raised outside request handling and reset the client
  connection. Both endpoints now require a JSON object, and settings updates
  additionally require an object, returning stable 400 responses.
- Finding `E2E-EXP-038` (P2): malformed or unknown integration selectors could
  fall through the checker's legacy CLI default and run every configured
  external probe. The HTTP contract now accepts only a list of known string
  identifiers; invalid input is rejected before any checker runs.
- Review finding fixed before commit: the shared live-server fixture inherited
  developer integration credentials. It now uses an isolated `.env` and drops
  all known external URLs/tokens so adversarial tests cannot contact real
  systems even when the parent shell is configured.
- Validation: 17 focused regressions failed before the fixes and passed after;
  the final settings/UI/integration/token/SSO/adversarial compatibility run
  passed 245 tests. Python compilation and high-signal Ruff checks passed. A
  transient Windows reset while reading a deliberately rejected 2 MB request
  passed alone and in the final suite. A post-review ownership refinement then
  passed 80 settings/properties/integration checks and 16 live-API regressions.
  Detailed review:
  `docs/reviews/exploratory-e2e-iteration-015.md`.
- Next slice: API and CLI parity, including dashboard APIs, `bin/qa.py`, hooks
  and the TaskEvent receiver.
