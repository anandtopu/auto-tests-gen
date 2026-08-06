# B3 Artifact Reuse — Per-File Review

## Scope

PRD B3 on `codex/test-knowledge-a1-a2`: durable reuse identity and restore,
B1 lookup, phase-wrapper ordering, run records, cost/explain surfaces, settings,
tests, and documentation.

## Findings

| ID | Severity | File | Finding | Resolution |
| --- | --- | --- | --- | --- |
| B3-R1 | P1 | `engine/lib/artifact_reuse.py` | Initial product collection assumed plans/testdata lived below the checkout, rejecting valid `AIQE_STATE_DIR` placement. | Manifests now carry canonical logical paths and resolve physical destinations through `app_paths`; relocation round trip is pinned. **Fixed.** |
| B3-R2 | P1 | `engine/phases/run_phase.sh` | Two caches could claim the same avoided phase if durable lookup ran independently of the phase cache. | Phase cache remains first; on hit B3 records ownership only and exits with zero artifact count. Durable lookup occurs solely after a miss. **Fixed.** |
| B3-R3 | P1 | `engine/lib/artifact_reuse.py` | Stored product paths needed a strict allowlist and link-aware containment before write. | Restore accepts only the phase's canonical declared file/directory products and checks their resolved configured base; traversal, undeclared descendants, and unsafe links are refused. **Fixed.** |
| B3-R4 | P2 | `engine/lib/cost_report.py`, `engine/lib/explain.py` | Malformed optional attribution could raise during aggregation or create unbounded explanation rows. | Counts are non-negative and type-checked, token bases are closed, and explain caps typed events. **Fixed.** |
| B3-R5 | P2 | `engine/lib/artifact_store.py` | A valid reference with a wrong declared size was skipped repeatedly during lookup. | The bad reference is quarantined and never returned. **Fixed.** |

## Validation

- New B3 module and adversarial tests pass Ruff.
- 106 focused tests pass across reuse, B1, phase cache, cost, explain, runner,
  run-record, and isolation surfaces.
- The complete registry compatibility suite passes 1,398 tests.
- No generated/runtime output is in scope.

## Open questions

- M7's 60% target requires production run history; B3 provides truthful counters
  but does not claim the target has been reached.
