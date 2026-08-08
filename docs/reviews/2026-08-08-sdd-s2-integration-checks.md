# Cross-file Integration Checks: SDD-S2 journey actions and refusal contracts

Date: 2026-08-08

## Flow Checks

| Flow | Files Checked | Status | Evidence | Gap/Risk | Action |
|---|---|---|---|---|---|
| State → API → journey button | `spec_workflow.py`, `dashboard_server.py`, `dashboard.py` | Pass | Six-state API returned no row missing action/command/view; UI consumes those fields and key | Browser visual runtime unavailable | Retain served HTTP and source pins |
| Requirements refusal | `pipeline.sh`, `sdd_messages.py`, `plan_state.py`, queue/UI error paths | Pass | Exact builder text raised before pipeline work; Bash entry pin passes | None | None |
| Plan approval refusal | Same as above plus plan generation API | Pass | CLI and HTTP conflict surface the same `SystemExit` text | None | None |
| Strict coverage refusal | `spec_check.py`, `gate.sh`, `work_queue.py`, run/dashboard surfaces | Pass | Exit 8 prints scenario-specific builder text; queue preserves it | Warn/strict wording initially conflated | Fixed and adversarially pinned |
| Expired waiver | `spec_store.py`, `spec_check.py`, `dashboard_server.py`, `dashboard.py` | Pass | Expiry date, scenario, and renew-or-cover action share the builder | None | None |
| Stale drift | `spec_drift.py`, `plan_state.py`, dashboard plan API/UI | Pass | Persisted vanished surfaces feed the exact notification and UI record | Same-id surface changes initially invisible | Fixed and regression-tested |

## Contract Checks

| Contract | Producer | Consumer | Status | Notes |
|---|---|---|---|---|
| `action`, `command`, `action_view` | `spec_workflow.status` | `/api/spec-workflow`, journey JS | Pass | All six states non-empty; browser never switches on state for actions |
| `{kind, what, why, action, command, text}` | `sdd_messages.refusal` | plan gate, spec gate, drift, queue, API/UI | Pass | Closed five-kind set and exact-text fixtures |
| advisory versus refusal | `spec_check.mode/main` | gate output and queue | Pass | Warn reports; strict refuses and alone uses refusal wording |
| `stale_surfaces` | `spec_drift` | plan state and dashboard API | Pass | Scenario-to-surface map updates independently of scenario-id set |
| visible/machine view naming | `dashboard.py` | docs and navigation pins | Pass | **Plan → tests journey** remains visible; `specflow` remains machine id |

## Integration Findings

- **P1 completed — changing drift evidence was not a change when the scenario id stayed constant.** Change detection now includes the scenario-to-vanished-surface map and a two-step regression proves re-notification.
- **P1 completed — warn mode would have claimed a delivery refusal.** Neutral findings remain in warn; the canonical refusal is constructed only for strict exit 8.
- **P2 completed — computed API evidence could lose to durable data.** Computed fields now follow `**entry`, and malformed legacy maps degrade to an empty mapping.
- No open product-code P0–P2 integration finding remains.
