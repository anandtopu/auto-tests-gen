# Test Plan — PROJ-301

**Ticket:** PROJ-301  
**Generated:** 2026-07-20  
**Phase:** testplan (Workflow B)  
**Status:** Draft — 2 open questions block full assertion coverage (see §7)

---

## 1. Scope & References

**Subject:** Percentage discount application on orders — `POST /v1/orders/{id}/discounts`  
**Affected application repo:** `orders-api` (domain: checkout, orders)  
**Test repo(s) in scope:** `e2e-api-tests-1` (layer: api, framework: playwright-api, suites/)

| Artifact | Location |
|---|---|
| Analyze contract | `out/analyze.contract.json` |
| OpenAPI spec | `openapi/orders.yaml` (cloned to `workspace/src/` at run time) |
| PRD / Confluence | `confluence:discounts-prd` |
| Estate knowledge | `AGENTS.md` |

All six behaviors (B1–B6) are `layer: api`. No UI behavior is specified in the contract; no UI scenarios are generated.

---

## 2. Existing Coverage (from catalog — delta)

| Spec | Existing assertion | Behaviors covered |
|---|---|---|
| `e2e-api-tests-1::suites/orders/discount.spec.js` (PROJ-88) | `POST /v1/orders/1/discounts` returns 2xx; applies % discount | B1 (2xx status ✓; recalculated-total field ✗ — blocked by OQ-2) |

**Delta — not yet covered:**

| Behavior | Gap |
|---|---|
| B1 — recalculated total field | Existing PROJ-88 does not assert the response body field; blocked pending OQ-2 |
| B2 — lower boundary (discount=1) | No test |
| B3 — upper boundary (discount=90) | No test |
| B4 — rejection at 0 (below lower bound) | No test |
| B5 — rejection at 91 (above upper bound) | No test |
| B6 — rejection for negative values | No test |

---

## 3. Risk Assessment

| Risk | Severity | Rationale |
|---|---|---|
| Off-by-one on boundaries (0/1 and 90/91) | High | Financial calculation; classic fence-post defect; two boundary pairs with no existing boundary tests |
| Recalculated total assertion absent | High | Core acceptance criterion; cannot be verified until OQ-2 is resolved (field name unknown) |
| 400 response body schema unspecified | Medium | S3–S5 can assert HTTP status only; error message format unknown (OQ-3) |
| Discount stacking undefined (AC-3) | Medium | No tests can be generated; regression risk if feature ships before spec is locked |
| Non-numeric / edge inputs unspecified | Low-Medium | null/string/float handling not stated (OQ-4); no scenarios generated |

---

## 4. Test Scenarios

> **Convention:** B1 is already exercised by PROJ-88 (2xx path). The recalculated-total assertion from B1 is added as an extension of that spec (`extend PROJ-88`). New boundary and rejection scenarios are S1–S5.

| ID | Title | Layer | Target test repo | Behavior ref | Data needs |
|---|---|---|---|---|---|
| extend PROJ-88 | PROJ-301: extend PROJ-88 — assert recalculated order total in 2xx response | api | e2e-api-tests-1 | B1 | Synthetic order with known pre-discount total; discount in [1, 90]; response body field name (blocked — OQ-2, use `test.fixme` skeleton) |
| PROJ-301-S1 | PROJ-301: lower boundary discount value 1 is accepted (2xx) | api | e2e-api-tests-1 | B2 | Synthetic order id; `discount: 1` |
| PROJ-301-S2 | PROJ-301: upper boundary discount value 90 is accepted (2xx) | api | e2e-api-tests-1 | B3 | Synthetic order id; `discount: 90` |
| PROJ-301-S3 | PROJ-301: discount value 0 (one below lower bound) returns 400 | api | e2e-api-tests-1 | B4 | Synthetic order id; `discount: 0`; 400 body schema unknown (OQ-3 — assert status only) |
| PROJ-301-S4 | PROJ-301: discount value 91 (one above upper bound) returns 400 | api | e2e-api-tests-1 | B5 | Synthetic order id; `discount: 91`; 400 body schema unknown (OQ-3 — assert status only) |
| PROJ-301-S5 | PROJ-301: negative discount value returns 400 | api | e2e-api-tests-1 | B6 | Synthetic order id; `discount: -1`; 400 body schema unknown (OQ-3 — assert status only) |

---

## 5. Test Data Strategy

- All order IDs are synthetic fixtures seeded by the test suite setup (`before`/`beforeEach`). No real customer or production data.
- Pre-discount totals must be deterministic so the `extend PROJ-88` recalculated-total assertion can compute expected values (once OQ-2 is resolved).
- Boundary inputs (`1`, `90`, `0`, `91`, `-1`) are hardcoded literals — no randomisation, to keep the scenarios reproducible.
- No credentials, PII, or real payment data in fixtures.
- Non-numeric inputs (null, string, float) are out of scope until OQ-4 is resolved; do not generate test data for them.

---

## 6. Entry / Exit Criteria

**Entry:**
- `out/analyze.contract.json` present and schema-valid.
- `e2e-api-tests-1` workspace checked out on branch `test/PROJ-301-ai-qe`.
- `orders-api` OpenAPI spec available at `workspace/src/openapi/orders.yaml`.

**Exit (gate must pass before PR):**
- All 5 new specs + 1 extension land in `e2e-api-tests-1::suites/orders/`.
- Every new spec has a matching catalog sidecar entry in `catalog/generated.jsonl` (born-mapped; gate exit 4 rejects unmapped tests).
- `test.fixme` skeletons used wherever OQ-2 or OQ-3 block assertions — no guessed field names.
- Gate passes all ordered checks: scope → born-mapped → lint → execute → secret scan.
- `GATE_STATUS=COMMITTED` emitted.

---

## 7. Open Questions

| # | Source | Blocker for |
|---|---|---|
| OQ-1 | AC-3 — "stacking TBD" | No tests can be generated for discount stacking; unresolvable until AC-3 is specified |
| OQ-2 | AC-1 — "total recalculated" | `extend PROJ-88` recalculated-total assertion; field name and sync/async behaviour unknown — `test.fixme` skeleton only |
| OQ-3 | AC-2 — 400 response body | S3, S4, S5 can assert HTTP status code only; error message field and format unknown |
| OQ-4 | AC-2 — non-numeric inputs | No scenarios generated for null / string / float discount values; out-of-scope until stated |
