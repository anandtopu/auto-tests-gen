# Review summary: SDD usability PRD

Date: 2026-08-08
Reviewer: Codex
Branch/Commit: `codex/test-knowledge-a1-a2` at `baf9f5c` before SDD work

## Overall status

| Area | Status | Notes |
| --- | --- | --- |
| Per-file pass | Completed | Product, state, UI, wizard, gate, governance, docs, and test surfaces reviewed |
| Cross-file integration pass | Completed | State, vocabulary, refusal, policy, approval, and wizard flows traced |
| Tests/build checks | Completed for S1 | Focused, adjacent, isolated failures, render/HTTP, adapter/adversarial/eval checks completed |
| Release readiness | S1 ready | S2–S4 remain planned; browser-only inspection is an environment residual |

## Findings

The PRD's seven diagnosed gaps are confirmed by repository evidence. No new
engine capability is needed. The implementation should remain a label/message/
composition layer over `spec_workflow`, plan/spec state, existing settings
stores, and gate decisions.

The v2 corrections are material and must remain pins: corrected pending-state
labels, signed/prose separation, engine-resolved governance, non-circular term
coverage, warn/strict distinction, a named refusal-builder home, and inclusion
of the getting-started walkthrough.

## Deferred evidence

- Q1–Q4 require product/QE or pilot-user decisions.
- M6 is a human comprehension/support baseline and must not be fabricated from
  automated fixtures.

## Next actions

1. Commit and push SDD-S1 after cached-diff validation.
2. Continue with SDD-S2 after remote parity is verified.
