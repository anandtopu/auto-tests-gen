# Cross-file integration checks: SDD usability PRD

Date: 2026-08-08

## Flow checks

| Flow | Files checked | Status | Evidence | Action |
| --- | --- | --- | --- | --- |
| State computation -> API -> dashboard label | `spec_workflow.py`, `dashboard_server.py`, `dashboard.py` | Partial before S1 | API already exposes state/blocker/action; UI renders raw names | Add presentation map only and pin exact states |
| Term definition -> UI -> newcomer docs | dashboard and three named docs | Missing before S1 | No glossary/markup source exists | Add one safe renderer and bidirectional coverage pin |
| Requirements/plan refusal -> CLI/UI | `plan_state.py`, `spec_check.py`, `pipeline.sh`, dashboard | Partial | Correct actions are distributed across messages | S2 shared builder and fixtures |
| Governance resolver -> level display/write | `spec_workflow.py`, settings stores, dashboard | Missing | Resolved knobs exist across environment and org config | S3 maps effective values; unmatched stays Custom |
| Approval -> signed/prose confirmation | plan state/spec store -> dashboard | Partial | Fact exists but confirmation is generic | S4 derive benefit/exemption copy from truth |
| Wizard -> gated criteria step | governance -> `wizard_status.py` -> dashboard | Missing | Wizard starts at plan today | S4 conditional step, off/on pins |

## Contract checks

| Contract | Producer | Consumer | Status |
| --- | --- | --- | --- |
| Six machine states | `spec_workflow.STATES` | glossary/dashboard | Must remain exact and presentation-only |
| Blocker/action/owner | `spec_workflow.status` | journey UI | Consume unchanged; no JavaScript inference |
| Signed vs prose | plan/spec state | approval and glossary copy | Must never collapse |
| Resolved knobs | engine resolvers via `governance()` | levels/governance/start panel | Must not re-read config independently |
| Refusal action text | future `sdd_messages.py` | Bash, Python, JS-facing API | One source in S2 |

No P0/P1 architecture blocker was found. The largest risk is a friendly label
drifting from the machine condition; S1's exact state/blocker pins address it.
