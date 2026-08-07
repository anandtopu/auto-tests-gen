# Exploratory E2E Status

This is the authoritative loop tracker for end-to-end exploratory testing. Each
iteration exercises one coherent feature slice against the served application,
records reproducible findings, fixes verified bugs, and pushes the iteration
before advancing.

Status values: `not started`, `in progress`, `passed`, `bug fixed`, `blocked`.

## Feature inventory

| Order | Feature slice | Surfaces | Status | Last evidence | Next focus |
| ---: | --- | --- | --- | --- | --- |
| 1 | Dashboard shell, startup, Overview, navigation | `make serve`, Overview KPIs, attention cards, theme, sidebar, queue header | passed | 2026-08-07: 15/15 primary destinations opened; no console warnings or loader failures; Overview catalog deep link and persisted theme cycle passed; queue badge semantics verified against six failed items | — |
| 2 | Guided PR run | PR form/URL parsing, validation, queue, progress, artifacts | not started | — | Happy path plus missing/invalid repo and PR inputs |
| 3 | Guided JIRA plan-first run | ticket input, draft plan, approval gate, generation, ticket link | not started | — | Happy path, invalid key, generate-before-approval |
| 4 | Intake and work queue | release fetch, inline ticket, plan-only, requeue/remove, drain | not started | — | Seed isolated queued/failed/done items and exercise lifecycle |
| 5 | Run progress and run review | phase state, failure details, release filters, reviewer decisions | not started | — | Seed committed/refused/quarantined runs; validate review transitions |
| 6 | Test plans | author, edit, versions, approve/request changes, export/link | not started | — | Boundary validation and stale-version behavior |
| 7 | Spec workflow | acceptance criteria, scenarios, waivers, drift and verification | not started | — | Waiver expiry/unmatched cases and release gate effects |
| 8 | Trace | PR/JIRA chronology, phase links, empty/unknown keys | not started | — | Cross-check trace with run/plan/artifact records |
| 9 | Cost | measured/simulated spend, caching savings, filters | not started | — | Empty data, simulated-only, mixed measured data |
| 10 | Artifacts | plan/data/tests/diff/code view, coverage report, export/publish | not started | — | Missing/corrupt artifacts and safe rendering |
| 11 | Activity and alerts | transaction filters, degraded log, rule evaluation, acknowledgement | not started | — | Seed success/failure/refusal/corrupt events |
| 12 | Test catalog | search, repo/status filters, mapping, CI health, orphan/quarantine | not started | — | Existing four-row seed plus empty/no-match searches |
| 13 | Repositories | app/test repo CRUD, scopes, curated guidance, contracts/routes | not started | — | Isolated synthetic repo; required-field and dependency checks |
| 14 | Settings and integrations | flags, adapters, credentials metadata, SSO/token modes | not started | — | Safe non-secret fixtures, invalid endpoints, clear/reset behavior |
| 15 | API and CLI parity | dashboard APIs, `bin/qa.py`, hooks/TaskEvent receiver | not started | — | Compare UI outcomes with supported API/CLI paths |
| 16 | Bootstrap/deployment/upgrade | onboarding, manifests, state bundle, migration/rollback checks | not started | — | Fresh isolated estate and compatibility smoke checks |

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
