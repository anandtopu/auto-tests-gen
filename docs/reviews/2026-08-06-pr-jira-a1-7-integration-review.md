# A1.7 TaskEvent Explicit PR Ticket — Cross-File Integration Review

## Scope

Trace: TaskEvent producer payload → receiver validation and replay identity →
work-queue ticket field → existing explicit-ticket normalization.

## Findings

| Dimension | Result |
|---|---|
| Correctness | PR `key` is optional and forwarded unchanged; JIRA still requires it. The receiver has no second key grammar or fallback path. |
| Security | The key is treated as untrusted data. Wrong types and malformed/prose values fail before queue or seen-state mutation. |
| Reliability | The exact historical PR digest is retained. Keyed and unkeyed redeliveries of the same PR head collapse to one accepted event, while corrected invalid deliveries remain retryable. |
| Product behavior | An explicit PR key now reaches the same queue `ticket` field used by API and wizard intake; omitted keys retain the pre-A1.7 queue shape. |
| Compatibility | JIRA digest behavior is unchanged, old PR producers need no update, and no new artifact or provider call is introduced. |
| Deployment | Schema and receiver changes are additive; there is no migration, dependency, port, service, or feature-flag change. Omitting `key` is the rollback path. |
| Coverage | Schema conditional behavior, exact legacy digest, cross-key replay, JIRA key distinction, successful propagation, malformed/non-string refusal, corrected retry, and omitted-key parity are covered. |

## Validation

Expanded and full suites are green; no open P0/P1/P2 findings remain.

## Residual Risk

The receiver's existing seen-check/enqueue/record sequence is not an atomic
transaction under truly concurrent identical requests. A1.7 preserves that
pre-existing behavior and does not widen the race; changing it requires a
separate reliability design across the queue and seen store.
