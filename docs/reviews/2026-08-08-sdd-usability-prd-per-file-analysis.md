# Per-file analysis: SDD usability PRD

Date: 2026-08-08

| File | Responsibility | Local status | Finding | Gap / action |
| --- | --- | --- | --- | --- |
| `docs/prd-sdd-usability.md` | Product contract | Ready | v2 corrects state-label optimism, signed/prose conflation, resolver drift, and circular glossary coverage | Preserve the four open product decisions explicitly |
| `engine/lib/spec_workflow.py` | Read-only state and resolved governance truth | Ready / unchanged | Already computes the specific blocker, action, owner, and real resolved knobs | Presentation must consume, never re-infer or mutate |
| `bin/dashboard.py` | Journey, wizard, settings, approval UI | S1 issue | Raw machine labels and ambiguous terminology are visible; glossary absent | S1 adds label/glossary layer; S2–S4 consume it |
| `engine/lib/wizard_status.py` | Guided journey composition | S4 gap | Requirements-gated first step is absent | Add conditional criteria step without new mutation path |
| `engine/lib/plan_state.py`, `spec_store.py` | Approval/signature facts and refusal sources | Ready | Structured and prose outcomes are distinguishable in existing state | B4 confirmation must inspect these facts |
| `engine/gate/spec_check.py`, `pipeline.sh` | Coverage and planning refusals | S2 gap | Actions exist but message wording is not one tested contract | Route through a named Python builder |
| `engine/lib/governance_page.py`, settings stores | Resolved and writable policy | S3 gap | Individual knobs are truthful; no single adoption-level mapping exists | Derive through governance and write only mapped knobs |
| `docs/ui-guide.md`, `use-cases.md`, `getting-started.md` | Newcomer path | S1 issue | Repeats the plan/test-file collision and raw states | Update and pin in S1 |
| Existing SDD tests | Mechanics and safety | Strong | State, gate, drift, waiver, settings, and API behavior are covered | Add comprehension-mechanism pins without weakening mechanics |

No engine-state change is justified by this PRD.
