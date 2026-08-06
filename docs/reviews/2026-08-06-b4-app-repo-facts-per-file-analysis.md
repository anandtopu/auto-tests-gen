# B4 Application-Repository Facts — Per-File Review

## Scope

PRD B4 on `codex/test-knowledge-a1-a2`: common facts schema, opt-in application
harvesting, the existing fallback guidance generator, compatibility tests, and
operator/architecture documentation.

## Findings

| ID | Severity | File | Finding | Resolution |
| --- | --- | --- | --- | --- |
| B4-R1 | P1 | `engine/lib/repo_guidance_gen.py` | An authored app-facts file added after generated scratch already existed could remain invisible because `ensure()` used create-once behavior. | Existing fallback guidance refreshes only for opted-in app repos; no-facts repos keep create-once behavior and repo-owned guidance still short-circuits generation. **Fixed.** |
| B4-R2 | P1 | `engine/lib/repo_facts.py` | An early deterministic cleanup removed the legacy test-repo `generated_at`, changing a non-B4 contract even when no app opted in. | Restored the test-repository timestamp; only application harvested documents carry the B4 byte-deterministic contract. **Fixed.** |
| B4-R3 | P2 | `engine/lib/repo_facts.py`, `engine/lib/repo_guidance_gen.py` | Non-mapping authored/catalog values could escape enrichment's total failure boundary. | Authored/harvested tiers normalize to mappings, catalog mapping/evidence shapes are checked, unreadable inputs degrade explicitly, and rendering tolerates malformed lists. **Fixed.** |
| B4-R4 | P2 | `engine/lib/repo_facts.py` | Missing input and an available empty surface needed different claims. | Surface facts use the closed `available` / `unavailable` / `not_configured` states; catalog availability is also explicit. **Fixed.** |

## Validation

- Ruff passes for the B4 implementation and focused test file.
- 35 focused facts/guidance tests pass.
- 83 cross-boundary facts, guidance, app-path, state-bundle, estate-generation,
  and knowledge-chunk tests pass.
- The complete established compatibility target passes 1,409 tests.

## Open Questions

- No P0–P2 B4 finding remains. App adoption is intentionally manual and
  per-repository; a dashboard structured editor remains outside this PRD item.
