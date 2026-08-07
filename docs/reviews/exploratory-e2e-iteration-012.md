# Exploratory E2E Review — Iteration 012

## Scope

This iteration completed Feature 11, Activity and alerts, through the real CLI,
authenticated API and served dashboard. It covered event filtering and CSV,
corrupt/partial evidence, formula-safe exports, rule validation and editing,
read-only previews, scheduled transitions, delivery, cooldown state,
resolution, malformed requests and restart persistence.

## Findings

| ID | Severity | Files | Finding | Impact | Fix |
| --- | --- | --- | --- | --- | --- |
| E2E-EXP-023 | P1 | engine/lib/alert_rules.py, bin/dashboard.py, bin/dashboard_server.py, bin/qa.py | Read-only alert evaluation persisted transitions. | Opening a view or listing alerts could consume the transition and suppress the scheduled notification. | Add a non-committing evaluation mode and use it on every read-only surface. |
| E2E-EXP-024 | P1 | engine/lib/event_log.py, engine/lib/alert_rules.py | Valid JSON with the wrong event/rule shape escaped the corruption boundary. | One malformed persisted row could hide Activity or abort all rule evaluation. | Validate event record shape; normalize malformed rule fields and timestamps with visible problems. |
| E2E-EXP-025 | P1 | engine/lib/alert_rules.py | Corrupt event lines were counted but an incomplete window could still report ok/firing. | Monitoring could claim health from evidence it knew was partial. | Mark enabled rules unevaluable and name the unreadable-line count. |
| E2E-EXP-026 | P2 | bin/dashboard.py, bin/dashboard_server.py | Alert Save and Test controls issued GET requests; body roots were assumed to be objects. | Operators could neither persist rules nor test delivery; malformed input could drop a connection. | Send JSON POST requests and reject non-object bodies with 400. |
| E2E-EXP-027 | P1 | engine/lib/alert_rules.py, bin/dashboard_server.py | UI saves erased evaluator-owned lifecycle and cooldown state. | An unchanged save could lose resolution and send duplicate notifications; a maintenance race could repeat the loss. | Preserve normalized state by unique id and perform merge plus atomic replace under one lock. |

## Reproduction and retest evidence

- Before E2E-EXP-023, a notification-disabled evaluation changed firing from
  false to true. The later notifying evaluation had no transition and sent
  nothing. Afterward Overview, Alerts GET and CLI previews leave state false;
  the maintenance tick records alert.fired and notify.sent exactly once.
- Two corrupt seed lines remained visible as a warning while three valid
  success/refusal/failure rows rendered. A JSON scalar and a dict without
  required event fields are now counted as corrupt instead of returned.
- The same two corrupt lines initially produced a firing alert. After
  E2E-EXP-025 the browser and CLI both reported unevaluable and named the two
  unreadable lines.
- Alert Save and Test initially displayed not found. After the POST fix, the
  browser saved a third rule, showed its matches-everything problem, and the
  channel test produced notify.sent in Activity.
- Before E2E-EXP-027, saving the unchanged firing rule reset its state, and
  aging the matching event produced no resolution. After the atomic server
  merge, firing and last-notified survived the save and restart; aging the
  event produced transition resolved plus alert.resolved.
- CSV output prefixed the synthetic actor =2+2, preventing spreadsheet formula
  interpretation while preserving the visible audit value.

## Pass 1 — per-file review

- engine/lib/event_log.py: invalid JSON and valid non-event JSON share one
  counted corruption path. Required strings match downstream CLI/UI contracts.
- engine/lib/alert_rules.py: preview and commit semantics are explicit.
  Malformed nested fields and timestamps cannot abort neighboring rules.
  Incomplete windows cannot report healthy. Edit merging preserves normalized
  state, generates unique ids and performs the read/merge/write under one lock.
- bin/dashboard.py: Overview uses a non-committing preview. Save/Test now use
  authenticated JSON POST through the common API wrapper.
- bin/dashboard_server.py: Alerts GET is read-only; mutation bodies require
  objects; save delegates state ownership and atomicity to the rule library.
- bin/qa.py: alert listing is non-committing and retains unevaluable reasons.
- Tests pin each discovered failure, server survival, UI mutation wiring,
  formula-safe CSV, lifecycle preservation and corrupt evidence behavior.

## Pass 2 — cross-file review

- Correctness: UI/API/CLI share event filters and rule evaluation. Preview
  status matches the scheduled evaluator without changing its next transition.
- Security: request bodies are shape-checked, UI values stay escaped, CSV
  formula prefixes remain intact, and no real notification channel was used.
- Reliability: malformed rows are isolated, partial logs are never healthy,
  lifecycle state survives edits/restarts, and concurrent save/evaluate writers
  share the existing cross-process lock and atomic replacement.
- Deployment: no dependency, migration, manifest or external service changed.
  Existing rule documents remain compatible; missing state receives defaults.
- Coverage: seven focused pre-fix failures captured the main defects. The final
  90 focused and 233 adjacent checks cover log safety, rules, live APIs,
  dashboard loaders, notifications, isolation and established regressions.

## Seed and cleanup review

All state lived under ignored out/exploratory-e2e-iter12. The mock Notify port
was used; no Slack, email, production service or customer record was contacted.
The seed directory, browser tab and exact server processes were removed.

## Residual risk

- Real Slack/email delivery was intentionally not exercised; mock delivery and
  adapter contract tests passed.
- Manual alert acknowledgement is not a documented product feature. The stale
  tracker label was corrected to the implemented firing/cooldown/resolution
  lifecycle rather than manufacturing an unsupported workflow.
- No blocker remains for Feature 11. Feature 12, Test catalog, is next.
