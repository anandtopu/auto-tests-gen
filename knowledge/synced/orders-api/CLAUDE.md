# orders-api â€” conventions for AI-generated E2E tests

Repo-local guidance: this file ships with the service and is merged into the
estate AGENTS.md automatically, so every generation phase sees it.

- All endpoints are versioned under `/v1/` â€” never generate calls to unversioned paths.
- Discounts: `POST /v1/orders/{id}/discounts` returns **201** with the discount
  echoed back; invalid codes return **422** (not 400).
- Order ids in test data must use the `ord-` prefix (e.g. `ord-1042`).
- Auth: requests carry `X-Api-Key`; test env injects it via fixture, never inline.

