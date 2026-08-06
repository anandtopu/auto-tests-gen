# B1 Artifact Store — Per-File Review

## Scope

Branch `codex/test-knowledge-a1-a2`; PRD B1 implementation across the artifact
store, path/configuration surfaces, retention CLI, tests, and operator/architecture
documentation. Generated runtime output under `out/` and `reports/` was excluded.

## Findings

| ID | Severity | File | Line | Finding | Impact | Recommended fix |
| --- | --- | --- | ---: | --- | --- | --- |
| B1-R1 | P1 | `bin/qa.py` | `cmd_prune` | Retention validation originally occurred after old run records were deleted. | A zero or malformed artifact-retention setting could partially delete history and then fail. | Validate both retention values before enumerating or deleting any record. **Fixed.** |
| B1-R2 | P2 | `engine/lib/artifact_store.py` | `put`, `_read_ref` | A UUID collision could replace a reference, and valid-JSON metadata damage was not independently detectable. | Append-only provenance could be lost or silently misattributed. | Allocate a free reference name under lock and digest canonical reference metadata. **Fixed.** |
| B1-R3 | P2 | `engine/lib/artifact_store.py` | `prune` | Sweeping after quarantining a corrupt reference could remove the only blob that reference described. | Recovery evidence could become incomplete. | Skip blob sweeping while quarantined reference evidence exists. **Fixed.** |
| B1-R4 | P2 | `engine/lib/artifact_store.py` | `put` | A generic repo-guidance kind did not prove the input was generated tier. | A future caller could persist repo-owned guidance contrary to B1.5. | Require `source_tier=generated` for repo guidance. **Fixed.** |

## Validation

- Focused Ruff: new implementation and adversarial test files pass.
- Focused pytest: 81 tests pass across artifact store, paths, settings,
  maintenance, CLI pruning, isolation, and writable-store pins.
- `git diff --check`: pass before broad validation.

## Open Questions

- D4's product retention-window choice remains open; the implementation default
  follows the existing 200-run policy and is independently configurable.
- Producer capture, state-bundle profiles, and historical explain are B2 scope.
