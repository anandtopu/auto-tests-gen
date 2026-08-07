# A4 Implementation Plan — Discovery Evaluation

Date: 2026-08-06
Status: Implemented and validated
PRD: `docs/prd-pr-jira-fused-context-multi-agent.md` §5 A4
Branch: `codex/test-knowledge-a1-a2`

## Acceptance mapping

| Requirement | Implementation | Verification |
|---|---|---|
| A4.1 labelled fixture coverage | Add a versioned QE-owned set covering explicit, branch-only, title/description-only, commit-only, absent, invalid, and conflicting keys. Pin the fixture bytes from the labels file. | fixture ownership, hash, coverage-class, and schema tests |
| Validation-aware measurement | Run the production `ticket_discovery.extract/resolve` functions with labelled Tracker outcomes; never count an unvalidated candidate as a discovery. | invalid-key and missing-validation adversarial tests |
| A4.2 per-signal precision/recall | Aggregate true/false positives and false negatives independently for explicit, branch, title/description, and commits. | metric-math test and exact per-signal result pins |
| Correct refusal | Represent an expected ambiguous refusal as a positive decision, not a missed ticket; a guessed key becomes both a false positive and false negative. | conflict fixture and wrong-guess metric test |
| `make eval` reporting | Add a discovery-eval step and scorecard rendering with the measurement state shown beside every figure. | Makefile/source pin and CLI artifact test |
| M1 ≥95% in mock | Enforce the PRD's precision floor while also failing any exact fixture outcome or signal-label mismatch. | evaluation result and regression tests |

## Completion evidence

- Seven hash-pinned fixtures cover explicit, branch-only,
  title/description-only, commit-only, no-key, invalid-key, and conflicting-key
  behavior.
- The production extraction/resolution functions report precision and recall of
  1.00 for all four signals. M1 precision/recall is 1.00 with 1/1 correct
  refusal and 7/7 exact outcomes, labelled `simulated`.
- `make eval` executes the evaluator and scorecard rendering. Fixture tampering,
  missing validation labels, path traversal, threshold weakening, repeated
  ticket identity, and wrong-guess accounting have focused coverage.
- Changed-file Ruff, JSON parsing, the integrated evaluation chain, and the
  complete registry suite pass. The full suite contains 1,466 tests.
- Two-pass review found three P2 integrity/documentation gaps and one P3 lint
  issue; all are fixed. No P0, P1, or P2 finding remains open.

## Implementation sequence

1. Define the versioned fixture and label schemas with QE ownership and a SHA-256
   pin between inputs and expected decisions.
2. Implement a deterministic evaluator over the production discovery functions.
3. Add per-signal metrics, final-decision metrics, and explicit correct-refusal
   accounting.
4. Integrate the evaluator into `make eval`, `make review`, and the scorecard.
5. Add unit, integration, tamper, and adversarial tests; document operation and
   evidence.

## Safety and rollout

The evaluator is read-only with respect to run/queue state and writes only its
derived result under `eval/results/`. Fixture-supplied Tracker verdicts are
synthetic evidence, so every rendered number is labelled `simulated`; they
prove deterministic policy and regression plumbing, not real-estate accuracy.
`AIQE_PR_TICKET_CONTEXT` remains default off until a QE-owned real-estate label
set independently clears the same threshold. No runtime flag, adapter, store,
port, dependency, or migration changes in A4.
