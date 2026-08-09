# Review summary: SDD-S4 wizard and approval benefit

Date: 2026-08-08
Branch: `codex/test-knowledge-a1-a2`

## Outcome

SDD-S4 is implemented and ready to push. The JIRA wizard now shows the missing
acceptance-criteria stop only when its resolved gate is active, with the block
reason and the existing approve action. Approval confirmations state benefits
only when a current valid structured spec is actually signed; prose and
signature-mismatch cases state their exemptions.

## Two-pass findings

| ID | Severity | Status | Finding | Resolution |
| --- | --- | --- | --- | --- |
| S4-01 | P1 | Fixed | Wizard plan approval omitted the revision required by the existing concurrency boundary, so a ready plan could not be approved from the journey | Fetch the current plan snapshot and submit its authoritative revision through the existing status route. |
| S4-02 | P1 | Fixed | An approved requirements status with no matching signature could have appeared complete | Require status, signed hash, current hash, and constant-time equality; otherwise block and offer re-approval. |
| S4-03 | P2 | Fixed | A criteria approve button inferred from mode or persisted after a target reset could contradict authoritative wizard state | Return the action only on an approvable criteria step, render from that action, and hide it on reset/absence. |
| S4-04 | P2 | Fixed | Warn enforcement could be described as holding generation, collapsing the PRD's warn/strict distinction | Strict says held; warn says gaps are reported and generation is not held; off makes no enforcement claim. |
| S4-05 | P3 | Residual | No visual browser click-through was available | Retain generated-page/source pins and live API evidence; perform visual confirmation in a later exploratory UI pass. |

## Validation evidence

| Check | Result |
| --- | --- |
| Wizard + usability focused set | 69 passed |
| Focused + adjacent requirements/PR-plan set | 86 passed |
| Authenticated API + lifecycle/signature/gate/drift set | 249 passed |
| M4 gate-branch mutation | Expected two gate-on failures; restored test passed in the final focused run |
| Python compilation | Pass |
| Repository-wide Ruff | 91 existing findings; no clean result claimed |

## Next eligible item

SDD-FINAL — broad verification and status reconciliation, unless the latest
remote PRD changes plan order or dependencies.
