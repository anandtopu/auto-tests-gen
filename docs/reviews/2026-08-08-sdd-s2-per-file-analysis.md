# Per-file Analysis: SDD-S2 journey actions and refusal contracts

Date: 2026-08-08

| File | Responsibility | Local Status | Findings | Test/Validation Gap | Action |
|---|---|---|---|---|---|
| `engine/lib/sdd_messages.py` | Closed, presentation-neutral refusal wording | OK | Fields are bounded, single-line, and cover the five PRD kinds | None | Keep as the sole wording owner |
| `engine/lib/spec_workflow.py` | Read-only state, blocker, owner, action, command, destination computation | OK | All six states return one complete action tuple | None | No action inference in browser code |
| `engine/lib/plan_state.py` | Requirements and plan approval authority | OK | Raises exact shared contracts without changing gate decisions | None | None |
| `engine/pipeline.sh` | Bash entry to plan/requirements gates | OK | Ticket gates use the builder CLI; PR requirements exemption remains direct and explicit | None | None |
| `engine/gate/spec_check.py` | Coverage enforcement | Issue fixed | Initial implementation said “Delivery refused” in warn mode | None after strict/warn pin | Build refusal text only for strict exit 8 |
| `engine/lib/spec_drift.py` | Detect and notify vanished surfaces | Issue fixed | Same stale scenario id with a different vanished surface did not count as changed | None after surface-change regression | Persist and compare scenario-to-surface evidence |
| `engine/lib/work_queue.py` | Preserve actionable queue failure reason | OK | Shared contracts are returned byte-for-byte within a bounded 1,000-character ceiling | None | None |
| `bin/dashboard_server.py` | Workflow and plan evidence APIs | Issue fixed | Computed refusal fields could be overridden by durable-entry keys; malformed legacy surface maps could fail rendering | None after API/adversarial suite | Canonical fields win; non-dict maps degrade empty |
| `bin/dashboard.py` | Journey buttons and refusal rendering | OK | Escapes API values, consumes backend action fields, and uses no state-to-action branch | Browser visual runtime unavailable | Served HTML/API check plus source pins |
| `registry/tests/test_sdd_usability.py` | B1/B3/M2/M3 contract pins | OK | Six states, five refusal types, Bash wiring, UI consumption, and queue parity covered | None | None |
| `registry/tests/test_spec_gate.py` | Strict/warn coverage behavior | OK | Pins neutral warning versus exact strict refusal | None | None |
| `registry/tests/test_spec_drift.py` | Drift evidence and notification lifecycle | OK | Pins exact shared text and same-id/new-surface re-alarm | None | None |
| `registry/tests/test_pr_plan.py` | PR exemption and ticket refusal | OK | Updated only the superseded message oracle | None | None |
| `docs/ui-guide.md`, `docs/user-guide.md`, `docs/architecture.md` | User and architecture contract | OK | Describe computed actions and the single wording owner | None | Currency suite passed |
| `docs/prd-sdd-usability-implementation-plan.md` | Backlog and acceptance evidence | OK | SDD-S2 mapped and evidence-backed | None | Advance to SDD-S3 after push |

## Notes

- `AGENTS.md` is generated context with an unrelated timestamp change and is not part of SDD-S2.
- No workflow mutator, enforcement default, machine state, endpoint path, or `data-view` id changed.
