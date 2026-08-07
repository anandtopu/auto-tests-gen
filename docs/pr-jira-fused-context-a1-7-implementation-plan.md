# A1.7 Implementation Plan — TaskEvent Explicit PR Ticket

Date: 2026-08-06
Status: Implemented and validated
PRD: `docs/prd-pr-jira-fused-context-multi-agent.md` §5 A1.7
Branch: `codex/test-knowledge-a1-a2`

## Acceptance mapping

| Requirement | Implementation | Verification |
|---|---|---|
| Optional `key` for PR TaskEvents | Clarify the shared schema and pass `key` to the existing `work_queue.add(..., ticket=...)` path. | schema pin and receiver enqueue test |
| One key grammar/validation path | Let `work_queue` reuse `ticket_discovery.normalize_explicit`; the receiver adds no regex or fallback. | malformed/prose key refusal and empty-queue assertion |
| Preserve pre-field PR replay identity | Keep the PR digest's historical empty key slot even when a PR event carries `key`; JIRA continues hashing its required key. | exact old-digest, keyed/unkeyed equality, JIRA inequality tests |
| Preserve redelivery behavior | Same PR/head events differing only by optional key dedupe to one accepted event. | sequential receiver integration test |
| Schema pin updated with implementation | Pin the JSON schema's PR optional/JIRA required contract in the same test file as receiver behavior. | schema structure assertions |

## Completion evidence

- The receiver forwards an optional PR `key` to the existing work-queue
  `ticket` field; omitted keys preserve the original queue item shape.
- PR idempotency keeps the exact historical empty key slot, so keyed and
  unkeyed redeliveries for the same head collapse to one event. JIRA continues
  to hash its required key.
- Malformed and non-string keys fail with HTTP 400 before the event is recorded
  as seen, allowing a corrected retry to succeed.
- Changed-file Ruff, JSON-schema parsing, and an expanded 126-test compatibility
  set passed. The complete registry suite passed with 1,457 tests.
- Two-pass review found one P2 runtime-validation gap; it was fixed with a
  receiver type check and adversarial coverage. No P0, P1, or P2 finding remains.

## Implementation sequence

1. Document `key` as required for JIRA and optional explicit linkage for PR.
2. Make PR idempotency's excluded-key rule explicit in `idempotency_key()`.
3. Forward a PR event's optional key through the existing queue validator.
4. Add exact hash, schema, enqueue, invalid-key, and duplicate-delivery tests.
5. Update user/architecture docs, run focused and broad checks, then complete
   per-file and cross-file review.

## Safety and rollback

TaskEvent text remains untrusted input. Invalid explicit keys return HTTP 400
before the event is marked seen, so a corrected sender retry is accepted.
Events without `key` retain the exact historical digest and queue behavior.
No migration, new store, port, adapter, or feature flag is introduced; omission
of `key` is the rollback path.
