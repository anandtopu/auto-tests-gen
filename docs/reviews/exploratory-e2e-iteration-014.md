# Exploratory E2E Review — Iteration 014

## Scope

This iteration completed Feature 13, Repositories, through the served browser
UI and authenticated HTTP API. It covered application and E2E repository CRUD,
service dependencies, declared scope and generated coverage, contract/route
metadata, team and curated guidance, validation, destructive guards and
restart persistence.

## Findings

| ID | Severity | Files | Finding | Impact | Fix |
| --- | --- | --- | --- | --- | --- |
| E2E-EXP-031 | P2 | `bin/dashboard_server.py` | Repository POST routes trusted any successfully parsed JSON shape. | A scalar, array or null reset the connection on all eight mutation endpoints. | Require an object before dispatch and return 400 for shape errors. |
| E2E-EXP-032 | P1 | `bin/dashboard_server.py` | `force=bool(value)` treated the string `"false"` as true. | A malformed client could bypass dependency protection and remove a covered repository. | Resolve remove/generate flags with the fail-safe `_json_flag` contract. |
| E2E-EXP-033 | P1 | `engine/lib/repo_admin.py` | Repository notes ignored relocated estate state. | Containers and isolated runs could write checkout-local guidance and generate knowledge from split state. | Resolve notes with `app_paths.knowledge_dir("repos")`. |
| E2E-EXP-034 | P2 | `bin/dashboard_server.py` | Unknown removal sections defaulted to the app remover. | A missing or misspelled discriminator could route a destructive request to the wrong repository type. | Require `section` to be exactly `app` or `test`. |

## Reproduction and retest evidence

- Before E2E-EXP-031, `[]`, `null`, `123` and `"repo"` closed the connection
  without a response on every repository mutation route. Afterward all 32
  route/payload combinations return 4xx and an authenticated `/api/repos`
  request immediately succeeds.
- Before E2E-EXP-032, removing covered `web-storefront-ui` with
  `"force":"false"` returned 200 and removed it from the isolated registry.
  Afterward it returns 400 and the repository remains present.
- Before E2E-EXP-033, importing `repo_admin` with an isolated state directory
  still resolved notes under the checkout. Afterward it resolves below the
  isolated `knowledge/repos`; the browser saved and reloaded team plus curated
  guidance there across restart.
- Missing, empty, plural and unrelated removal sections now return 400 before
  either repository remover is selected.
- The browser created `zz-explore-api`, dependent `zz-explore-ui`, and scoped
  `zz-explore-e2e`; displayed contracts/routes and generated coverage; edited
  metadata; rejected invalid dependencies/scopes; refused covered removal; and
  retained state after server restart.

## Pass 1 — per-file review

- `bin/dashboard_server.py`: object validation precedes all repository route
  field access. Destructive flags resolve toward false when unusable, and the
  removal discriminator is validated before function selection. Existing
  validation failures remain actionable 400 responses.
- `engine/lib/repo_admin.py`: the default notes path is unchanged, while
  `AIQE_STATE_DIR` and `AIQE_KNOWLEDGE_DIR` now relocate it consistently with
  curated and generated estate knowledge.
- `registry/tests/test_api_adversarial.py`: the live server owns isolated
  registry/catalog/knowledge outputs; only catalog JSONL data is seeded. Tests
  pin connection health, false-force preservation and strict section routing.
- `registry/tests/test_app_paths.py`: a subprocess import proves the module's
  import-time path honors a relocated state root.

## Pass 2 — cross-file review

- Correctness: UI and API agree on app/test records, dependencies, scope,
  generated covers, metadata and guidance before and after restart.
- Security: unknown JSON shapes and route discriminators do not reach mutation
  code; string false cannot authorize a destructive operation. Synthetic data
  contains no credentials or PII.
- Reliability: the adversarial server cannot touch the checkout registry or
  catalog. Malformed requests return bounded 4xx responses and leave the next
  request healthy.
- Deployment: no schema, dependency, manifest or external integration changed.
  Default checkout paths remain backward compatible; state-volume deployments
  now keep repository notes with the rest of the estate.
- Coverage: 100 adversarial/state tests and 215 broad repository/guidance/
  routing/catalog/UI/API tests passed.

## Seed and cleanup review

All Feature 13 mutations lived under ignored
`out/exploratory-e2e-iter14`. The local server bound only to `127.0.0.1` with a
test token and mock adapters. The server was stopped and browser tabs were
finalized after persistence verification.

## Residual risk

- Live SCM guidance sync was not invoked because it would require external
  repository credentials; mock/local generation, team notes and durable
  curated guidance were exercised instead.
- The destructive `force=true` path was intentionally not driven through the
  browser; fail-safe false handling and normal dependency refusal were tested.
- No blocker remains for Feature 13. Feature 14, Settings and integrations, is
  next.
