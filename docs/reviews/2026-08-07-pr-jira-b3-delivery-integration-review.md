# PR/JIRA B3 Delivery Policy — Cross-File Integration Review

Date: 2026-08-07

## Pass 2 — integration, security, reliability, deployment, coverage

### Correctness

- `off` suppresses review even with an inherited enable flag; `warn` preserves the measured rollout; `require` forces review and refuses final surface needs-work before critic/gate.
- The decision uses B2's final surfaced unresolved findings, not a later raw approve verdict.
- `review.on_unavailable: proceed|hold` applies only to required unavailable evidence; skipped zero-test runs are not misreported as approval.
- Refusal persists `review_delivery`, review findings, empty gates, and `overall: review_refused`; comments and progress consume the same facts.

### Security and authority

- The reviewer remains Read-only. Repair remains separately bounded to existing generated files.
- No file under `engine/gate/` reads reviewer or delivery output. The gate receives no LLM-derived order.
- No per-run agent-gate environment override exists; require cannot be disabled through `AIQE_TEST_REVIEWER=0`.
- Delivery evidence has closed enums, bounded strings/lists, consistency validation, and single-line shell/comment summary rendering.
- Human review-board status is never changed on the refusal branch; post-commit selection remains human.

### Reliability and failure semantics

- Run evidence is written before best-effort network comments/status calls.
- Malformed/missing reviewer evidence remains explicit unavailable; require follows the configured outage policy.
- Exit 78 has a documented operator meaning. Gate progress is skipped/blocked, not unknown or passed.
- Testcase learning runs only after real gate commits; early refusal has no commit to index.

### Deployment and compatibility

- New scratch evidence uses the existing writable `out/` location and durable run records use existing state paths; no new volume, secret, port, network dependency, or manifest change is required.
- Default `warn` plus disabled reviewer makes no reviewer call and writes no B3 delivery sidecar, preserving rollout artifact parity.
- Settings displays live org-config values read-only and directs policy changes to the audited estate configuration.

### Coverage

- Unit: policy/outage matrix, precedence, strict/tampered evidence, newline bounds.
- Integration: run record, PR markdown, progress states, report rollups, constitution rendering.
- Adversarial: gate-source scan, no-bypass scan, inconsistent outcome, named-fix requirement.
- Compatibility: final 1,559-test registry pass.

## Conclusion

The B3 implementation satisfies B3.1–B3.4 without moving LLM judgement into the deterministic gate. No open P0–P2 integration, security, reliability, deployment, or coverage finding remains.
