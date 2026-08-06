# B1 Artifact Store — Review Action Plan

## Release Gate

No open P0–P2 B1 findings. B1 may be committed when the broad registry suite,
focused Ruff, cached-diff check, commit, push, and upstream verification pass.

## Fix Queue

| ID | Priority | Owner | Action | Acceptance check |
| --- | --- | --- | --- | --- |
| B1-R1 | P1 | Platform | Validate retention before any deletion. | Invalid zero/non-integer values leave run records and artifacts intact. **Done.** |
| B1-R2 | P2 | Platform | Preserve append-only references and detect metadata corruption. | Collision-safe allocation and valid-JSON tamper test pass. **Done.** |
| B1-R3 | P2 | Platform | Keep GC conservative around quarantine. | Quarantine retention test preserves blobs and reports skipped sweep. **Done.** |
| B1-R4 | P2 | Platform | Enforce generated-only repo guidance. | Owned-tier attempt is rejected; generated tier succeeds. **Done.** |

## Follow-Up Backlog

- B2: capture phase artifacts, produce per-run manifests, serve historical
  `explain`, and implement portable-state profile rules.
- D4: QE/engineering management may tune `AIQE_ARTIFACT_KEEP_RUNS` from the
  implemented 200-run default when audit-policy evidence is available.
