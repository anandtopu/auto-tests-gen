# Test Plan — PROJ-301

> Rendered from `specs/PROJ-301/testplan.yaml` — the structured spec is the source of truth; edit scenarios there (or via the plan editor), not this file.

## Scenarios

### PROJ-301-S1 — boundary rejection
- layer: api · target repo: e2e-api-tests-1 · requirements: R1
- **Given** an order of $100 exists
- **When** a 91% discount is POSTed
- **Then** the API responds 422 and the order total is unchanged
- verify: response status is 422
- verify: order total unchanged after rejection
- data: d1

### PROJ-301-S2 — stacking on an already-discounted order
- layer: api · target repo: e2e-api-tests-1 · behavior: B1
- data: d2

### PROJ-301-S3 — discount POST without orders:write scope
- layer: api · target repo: e2e-api-tests-1 · behavior: B2
- data: d3

## Open Questions
- stacking undefined
