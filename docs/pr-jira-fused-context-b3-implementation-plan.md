# B3 - Agent-review delivery policy implementation plan

Date: 2026-08-07
Status: Implemented
Source: [prd-pr-jira-fused-context-multi-agent.md](prd-pr-jira-fused-context-multi-agent.md)

## Acceptance mapping

| Criterion | Implementation | Verification |
| --- | --- | --- |
| B3.1 consequences | `test_reviewer.policy/enabled/delivery` makes `off` suppress review, `warn` proceed, and `require` refuse final needs-work before critic or gate. | Closed-policy and delivery-matrix tests; run-record/comment integration; pipeline-order pin. |
| B3.1 unavailable | Under `require`, `review.on_unavailable: proceed|hold` is normalized independently. | Unavailable matrix and tamper tests. |
| B3.1a no bypass | `require` forces the reviewer on even when `AIQE_TEST_REVIEWER=0`; only org-config chooses the consequence. | Environment-precedence and production-source scan tests. |
| B3.2 rollout | Settings renders the live policy and explains the measured warn-then-require rollout. | Settings source pin and UI guide checks. |
| B3.3 pre-gate only | Enforcement runs after repair and before critic/gate; refusal records, comments, and exits 78. | Shell ordering, progress-state, and no-commit evidence tests. |
| B3.4 constitution | C14 separates reviewer policy from the deterministic gate and C2 critic, with one pin per subclaim. | Constitution boundary, read-only, status, and distinctness pins. |

## Design boundary

- Policy is enforced by the pipeline, never by `engine/gate/`.
- The delivery artifact is strict and run-scoped; tampering cannot launder refusal.
- Exit 78 means the gate was never attempted: review failed and gate skipped.
- `off` wins in the suppressing direction and `require` in the enforcing direction.

## Review and validation

The multi-pass review fixed four P2 findings: run-scope evidence, refusal
misclassification, multiline summary hardening, and default-off artifact
parity. It also reconciled the post-commit learning order pin. Final focused
verification passed 101 tests; fatal Python lint passed; the exact final tree
passed all 1,559 registry tests in 829.88 seconds. Detailed evidence is in the
three `2026-08-07-pr-jira-b3-delivery-*` reports under `docs/reviews/`.
