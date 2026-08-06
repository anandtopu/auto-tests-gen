# B1 Artifact Store — Cross-File Integration Review

## Scope

Control/data flow from settings and `app_paths` through artifact writes/reads,
`qa.py prune`, nightly maintenance, deployment placement, test isolation, and B2
boundaries.

## Findings

| ID | Severity | File | Line | Finding | Impact | Recommended fix |
| --- | --- | --- | ---: | --- | --- | --- |
| B1-I1 | P1 | `bin/qa.py`, `engine/lib/artifact_store.py` | retention path | Deletion safety and store pruning needed one prevalidated contract. | Partial retention failure could split run and artifact history. | Parse and validate all retention inputs before the first delete. **Fixed.** |
| B1-I2 | P2 | `engine/lib/app_paths.py`, `registry/tests/conftest.py` | store placement | A new writer needed both deployed-volume placement and day-one test redirection. | Read-only-root deployment failure or test writes into operator evidence. | Resolve via `app_paths`, expose `AIQE_ARTIFACTS_DIR`, redirect it suite-wide, and pass the class-level pin. **Fixed.** |
| B1-I3 | P2 | `engine/lib/artifact_store.py` | corruption/retention boundary | Quarantine and garbage collection can conflict when provenance is unreadable. | A cleanup job could destroy recoverable content. | Make mark/sweep fail conservative while quarantine evidence exists. **Fixed.** |

## Validation

| Boundary | Evidence |
| --- | --- |
| Correctness | SHA-addressed dedup, multiple references, UTC provenance, digest validation, missing/corrupt refusal. |
| Security | Secret-assignment, bearer, credentialed-URL, private-key, configured-secret, size, kind, scope, and owned-guidance adversarial cases pass. |
| Reliability | 12 concurrent writers retain 12 references and one blob; all mutations are under `fs_lock`; atomic writes and conservative quarantine are exercised. |
| Deployment | Default path is `reports/agent-artifacts`; `AIQE_STATE_DIR` and the narrower `AIQE_ARTIFACTS_DIR` relocate it; default-off writes nothing. |
| Retention | CLI/maintenance integration keeps newest producing runs, preserves shared blobs, sweeps only unreferenced blobs, and rejects invalid values before deletion. |
| Test isolation | Suite redirect and `test_no_writable_state_store_still_points_at_the_estate` pass. |

## Open Questions

- B2 must decide which reusable blobs belong in full versus knowledge-only state
  profiles; B1 intentionally does not pre-empt that policy.
