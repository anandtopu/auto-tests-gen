---
name: e2e-api-conventions
description: API E2E authoring conventions for playwright-api suites in this estate.
---
# API E2E Conventions
- Use the shared request client in suites/_lib/client.ts (auth, base URL, tracing).
- Assert: status, response schema (zod schemas in suites/_lib/schemas/), and the
  specific business fields the scenario targets — not whole-body snapshots.
- Negative cases: invalid payload, authz failure, and boundary values for every
  mutating endpoint touched.
- Idempotent setup/teardown via API, never via UI.
