# Exploratory E2E Review — Iteration 016

## Scope

This iteration completed Feature 15, API and CLI parity. It exercised the
authenticated dashboard API beside `bin/qa.py`, then drove the real token-gated
TaskEvent receiver through valid, duplicate, malformed, wrong-typed and
unauthenticated requests. Adjacent CI-result and OpenHands hook contracts were
included in compatibility validation.

## Findings

| ID | Severity | Files | Finding | Impact | Fix |
| --- | --- | --- | --- | --- | --- |
| E2E-EXP-039 | P2 | `bin/taskevent_receiver.py` | Parsed TaskEvents were not checked against the schema's object/property types before digesting. | Scalars and wrong-typed replay fields returned 500 or bypassed the published normalized-event contract. | Validate object shape and every known property type before mode requirements, digest or queue dispatch. |
| E2E-EXP-040 | P2 | `bin/taskevent_receiver.py` | Concurrent duplicates could both report `accepted=true` after racing the seen check. | Queue state stayed singular, but webhook senders received a false claim that two deliveries were accepted. | Treat the queue's non-fresh result as the authoritative duplicate signal and return a recorded no-op response. |

## Reproduction and retest evidence

- Before E2E-EXP-039, `[]`, `null`, `123` and a JSON string returned 500 from
  `/hooks/taskevent`; list-valued `repo` and numeric replay fields also returned
  500. A JIRA event with object-valued optional `pr` was accepted. All now
  return 400, and a valid request immediately afterward returns 200.
- Before E2E-EXP-040, two synchronized `handle_event` calls both observed an
  unseen digest and both returned `accepted=true`, while the queue contained
  one item. Afterward exactly one response is accepted and one is an explicit
  no-op, with the queue still containing one item.
- The real isolated receiver produced the sequence 401 unauthenticated, 400
  non-object, 400 wrong type, 200 accepted and 200 duplicate/no-op, with one
  durable queue record.
- The authenticated dashboard API exposed seven PROJ-301 trace events and the
  latest PR-orders-api-201 coverage run. CLI trace/artifacts rendered the same
  keys and run evidence; alerts and recent-status output agreed with API state.

## Pass 1 — per-file review

- `bin/taskevent_receiver.py`: validation is total for arbitrary JSON and runs
  before replay hashing. Required mode fields retain the work queue's stricter
  semantic validation. Non-fresh queue insertion is handled without starting
  autorun and records the digest for future redeliveries.
- `registry/tests/test_openhands_webhooks.py`: every HTTP case starts a real
  receiver with isolated queue/seen/OpenHands paths, asserts response codes and
  proves the endpoint remains usable after malformed input.
- `registry/tests/test_taskevent_ticket_link.py`: a barrier forces the precise
  concurrent unseen race while the real locked work queue proves one durable
  item and one accepted response.

## Pass 2 — cross-file review

- Correctness: `triggers/task-event-schema.json`, receiver validation,
  idempotency hashing and work-queue semantics now agree for PR and JIRA modes.
- Security: hook tokens remain distinct from UI tokens; malformed input is
  bounded before queueing; OpenHands observability events cannot enqueue work.
- Reliability: receiver errors remain retryable, duplicate queue insertion is
  a truthful no-op, body size/read deadlines and CI-result limits remain green.
- Deployment: no port, token, payload or persisted queue schema changed. Older
  valid senders and the historical PR replay digest remain byte-compatible.
- Coverage: 182 CLI/API/hook/body-limit/CI/work-queue tests passed, plus Python
  compilation and high-signal Ruff.

## Seed and cleanup review

Mutable receiver state lived only under ignored
`out/exploratory-e2e-iter16`. Both services bound to `127.0.0.1`; tokens and
events were synthetic, autorun was disabled, adapters stayed in mock mode and
no PII, real credential, customer data or production endpoint was used.

## Residual risk

- The dashboard/CLI comparison used the supplied synthetic demo estate rather
  than a production-sized run history.
- Reverse-proxy and load-balancer behavior is covered by application-level auth
  and body-limit tests, not a deployed ingress in this slice.
- No blocker remains for Feature 15. Feature 16, bootstrap/deployment/upgrade,
  is next.
