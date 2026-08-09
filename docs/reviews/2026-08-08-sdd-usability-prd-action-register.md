# Review action register: SDD usability PRD

Date: 2026-08-08

| ID | Severity | Status | Owner area | Finding | Evidence | Recommended action | Validation expected | Dependencies |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| SDD-01 | P1 | Completed | S1 vocabulary | User copy conflates structured plans and test files, and structured/prose approval guarantees | PRD D1/A1; dashboard raw copy | Shipped one term policy and signed/prose distinction | User-facing docs and rendered UI pins pass | none |
| SDD-02 | P1 | Completed | S1 glossary | No in-product definitions or markup coverage invariant | PRD D2/A2 | Added closed safe renderer, tooltips, glossary card, both-direction pin | Undefined/unused/escaping tests pass | SDD-01 |
| SDD-03 | P1 | Completed | S1 labels | Four raw middle-state names naturally read one milestone too optimistic | PRD D5/A3; `spec_workflow.py` conditions | Render corrected labels first with machine terms subordinate | Six behavioral state/blocker pins pass | none |
| SDD-04 | P1 | Completed | S2 refusals | Message actions were distributed and not contract-tested across CLI/UI | PRD D7/B3; S2 review | Shipped one Python message builder and five fixtures | CLI/UI parity tests pass | S1 |
| SDD-05 | P1 | Completed | S3 adoption | Three resolved knobs had no single honest effective level | PRD D3/C1; S3 review | Shipped one mapping over `governance()`, including Custom and warn badge | Mapping/write-boundary/API tests pass | S1 |
| SDD-06 | P1 | Open | S4 wizard | Requirements-gated users are blocked at an absent wizard step | PRD D4/B2 | Conditional criteria step derived from resolved governance | on/off wizard tests | S1, S2 |
| SDD-07 | P2 | Open | S4 approval | Generic approval confirmation does not state signed benefits or prose exemptions | PRD D6/B4 | Derive confirmation from actual plan/signature state | structured/prose tests | S1 |
| SDD-08 | P3 | Deferred | Product decisions | Q1–Q4 require product/pilot evidence | PRD §10 | Keep assumptions explicit; never invent human-comprehension results | Named owner decision | relevant slice |

## Status summary

| Status | Count |
| --- | ---: |
| Open | 2 |
| In progress | 0 |
| Completed | 5 |
| Deferred | 1 |
