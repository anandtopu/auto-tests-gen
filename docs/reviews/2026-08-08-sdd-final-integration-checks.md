# Cross-file Integration Checks: SDD usability final verification

Date: 2026-08-08

## Flow Checks

| Flow | Files Checked | Status | Evidence | Gap/Risk | Action |
| --- | --- | --- | --- | --- | --- |
| Ticket → criteria → plan → approval → tests → gate | Pipeline, plan lifecycle, spec store, mock journey | Pass | Isolated mock journey 5/5 and broad SDD partition | None found | None |
| Resolved governance → named level → user surfaces | Workflow, adoption mapping, dashboard/server | Pass | Mapping, authenticated API, UI, settings, and constitution tests | Q1/Q4 human decision remains | Deferred |
| Workflow state → plain label/action → wizard/UI | Workflow, wizard status, glossary, dashboard | Pass | M3/M4 fixtures and live API tests | Visual browser unavailable | Operational probe only |
| Approval → signature truth → confirmation | Plan state, spec store, server, dashboard | Pass | Structured off/warn/strict, prose, mismatch, and live approval tests | None found | None |
| Refusal producer → CLI/queue/API consumer | Shared builder, pipeline, gate, queue, server | Pass | Five-kind contract and adversarial tests | None found | None |
| UI queue test → worker → state/artifacts → assertion | UI test, queue worker, pipeline, path resolvers | Pass after fix | Real isolated test passed and checkout stayed clean; mutation failed without spec isolation | Run records lack a relocation knob | Exact before/after cleanup retained |
| Docs/plan/action register → shipped behavior | PRD, plan, UI/user docs, review register | Pass after fix | Currency tests and pushed S1–S4 commits | M6 and Q1–Q4 need humans | Explicitly deferred |

## Contract Checks

| Contract | Producer | Consumer | Status | Notes |
| --- | --- | --- | --- | --- |
| Six machine states | `spec_workflow.STATES` | API and dashboard | Pass | Stable; presentation labels remain subordinate |
| One authoritative UI action | Workflow / wizard status | Dashboard | Pass | Browser code does not infer workflow state |
| Signed versus prose | Spec hash + plan lifecycle | Approval confirmation | Pass | Hash mismatch fails closed to prose guarantees |
| Adoption write set | Adoption mapping | Settings route/store | Pass | Exactly three controls; warn/strict distinct |
| M6 evidence boundary | PRD | Final status/report | Pass | Unmeasured remains unmeasured |

## Integration Findings

- P1 fixed: the UI queue journey used a real-estate false oracle and polluted
  tracked spec artifacts despite passing.
- P2 fixed: delivery status drift left completed S4 items open in the PRD action
  register.
- P3 deferred: visual browser and human-comprehension evidence are unavailable;
  neither is represented as passed.
