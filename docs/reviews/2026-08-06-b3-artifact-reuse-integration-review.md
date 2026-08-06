# B3 Artifact Reuse — Cross-File Integration Review

## Boundaries reviewed

| Boundary | Result |
| --- | --- |
| Phase cache → durable reuse → provider | One ordered owner: local hit exits first; durable exact-input hit exits second; provider runs only after both miss. |
| Canonical inputs → B1 reference | Prompt/context content, run parameters, model, policy, adapters, schemas, extraction/materialization code, and generator version determine the SHA. |
| B1 package → filesystem product | Hash-validated package restores a fixed contract output plus only the allowlisted plan/testdata products through configured paths. Workspace/git phases are denied. |
| Event → run record → cost/explain | Hits, misses, phase-cache ownership, refusals, reasons, token count, and basis survive scratch cleanup. Cost mechanisms remain disjoint; explain is historical. |
| Security | B1 secret/size/kind checks apply; reference/blob hashes are verified; stored paths cannot traverse or escape configured product roots. |
| Reliability | Corrupt candidates miss safely, optional event writes cannot fail a completed restore, malformed historical attribution is tolerated, and default-off writes nothing. |
| Deployment | B1 full-state portability supplies the second-level cache after local phase-cache loss; logical paths relocate with `app_paths`. |
| Coverage | Exact/stale/version/provider identities, corruption, relocation, unsafe paths, denied phases, attribution, and feature-off behavior are exercised. |

## Findings fixed during integration

- B3-I1 (P1): relocated products were coupled to checkout-relative paths.
- B3-I2 (P1): savings ownership needed a hard control-flow order.
- B3-I3 (P1): restore needed declared-product plus resolved-base containment.
- B3-I4 (P2): damaged optional attribution needed total readers.

No open P0–P2 B3 finding remains after focused validation.
