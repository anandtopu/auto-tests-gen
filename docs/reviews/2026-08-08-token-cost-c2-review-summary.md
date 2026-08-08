# TCA-C2 review summary

## Outcome

Ready to commit. C2.1/C2.1a are implemented through the adapter boundary. The
engine has one normalized provider usage contract; it has no Anthropic branch.
Claude provides live organization cost evidence and every other adapter remains
honest when billing evidence is unavailable.

## Correctness and reliability

- Provider fractional cents use exact decimal arithmetic.
- UTC daily windows and pagination evidence are returned by the adapter for C3.
- Pagination is bounded and repeated cursors are rejected.
- Malformed/down responses become unavailable, never zero and never a partial sum.
- The engine validates state, provider, increasing UTC window, currency, basis,
  and finite non-negative Decimal amount.

## Security and deployment

- `ANTHROPIC_ADMIN_KEY` is separate from the LLM key, write-only in Settings,
  and documented as read-only organization usage/cost scope.
- No key, response body, or vendor error detail is printed.
- Local, properties, integration, and OpenShift surfaces declare the credential.
- Engine code is vendor-free; only the Claude adapter contains the endpoint.

## Coverage and residual risk

Focused tests: 165 passed. Broad relevant compatibility: 343 passed. Adapter
conformance, Python/shell syntax, and the live Make/mock journey passed. A real
Anthropic organization was not contacted; the exact official HTTP contract was
exercised against a local server. The full 1,700+ registry sweep timed out at
600 seconds without a result, while also revealing the tracked-plan isolation
bug fixed and independently retested here.

Advance to TCA-C3: align the returned provider window with durable reported-basis
ledger rows, compute drift and reconcilable fraction, and persist deterministic
comparison evidence without auto-correction.
