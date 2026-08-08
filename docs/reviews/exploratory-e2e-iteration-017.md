# Exploratory E2E Review — Iteration 017

## Scope

Feature 16 closes the exploratory inventory: first boot, onboarding, deployment
manifests, maintenance backup, portable state export/inspect/import, upgrade
compatibility and rollback boundaries. The review used the multi-pass release
workflow: each changed source/test/document was reviewed independently, then the
entrypoint → manifest → path resolver → bundle → receiving deployment flow was traced
end to end.

## Findings

| ID | Severity | Files | Finding | Impact | Fix |
| --- | --- | --- | --- | --- | --- |
| E2E-EXP-041 | P1 | `engine/lib/state_bundle.py` | Inspect did not verify hashes; import skipped mismatches after writing earlier members and exited successfully. | A corrupt rollback could leave a plausible half-estate while automation reported success. | Preflight exact membership, types, counts and all SHA-256 values before any target/lock mutation; fail inspect/import non-zero. |
| E2E-EXP-042 | P1 | `engine/lib/state_bundle.py` | Import trusted manifest paths and recognized only POSIX traversal. | A self-consistent hostile bundle could overwrite code, or escape with backslashes on Windows. | Enforce the export allowlist independently; require canonical POSIX names and reject both separator forms. |
| E2E-EXP-043 | P1 | `engine/lib/state_bundle.py`, `engine/lib/app_paths.py` | Restore joined most paths to `ROOT`; knowledge resolution treated caller `root` as `sub`. | Read-only deployments restored to the image or leaked knowledge directories beside the checkout instead of the state volume. | Resolve all mutable targets through `app_paths`; pass caller roots by keyword and pin registry/catalog/knowledge cross-root restoration. |
| E2E-EXP-044 | P2 | `engine/lib/state_bundle.py` | Dry-run acquired a mutation lock and created its parent directories. | A no-write rehearsal changed an empty volume and could make first-boot state look pre-existing. | Use a null context for dry-run and assert the target remains completely absent. |
| E2E-EXP-045 | P2 | `engine/lib/state_bundle.py`, portability docs | Export included image-owned schema/constitution, while policy restore conflicted with image upgrades. | Replace could freeze old runtime contracts or fail partway on a read-only root. | Exclude schema/constitution from new exports; accept but preserve legacy frozen members and carried policy. |
| E2E-EXP-046 | P2 | `registry/tests/test_onboard.py`, `registry/tests/test_api_adversarial.py` | Test isolation omitted generated skills; Windows RST while reading an optional 400 body erased the received status. | Tests polluted the working estate and the broad gate flaked despite correct server behavior. | Redirect `AIQE_SKILLS_DIR`; retain an HTTPError status when only its optional body read resets. |

## Pass 1 — per-file review

- `engine/lib/state_bundle.py`: export and import now share an explicit state
  allowlist; manifest membership, duplicates, types, path form and checksums are
  validated before target resolution. Old frozen members are readable but skipped.
  Dry-run has no filesystem side effect.
- `engine/lib/app_paths.py`: every mutable resolver accepts `root=`; frozen image
  paths remain under the checkout and configured state/per-path knobs still win.
- State/path tests: cover tampering, missing trust, duplicates, code injection,
  POSIX/backslash traversal, legacy frozen members, merge/replace/dry-run, live lock,
  artifact relocation and source-to-receiver portability.
- Harness tests: onboarding now isolates registry, knowledge, AGENTS and generated
  skills; HTTP status remains observable after an optional error-body reset.
- Documentation: architecture, operator portability contract and feature matrix now
  distinguish carried evidence, mutable restored state and image-owned policy/schema.

## Pass 2 — cross-file integration review

- Correctness: `app_paths` is the single location map for export and import; archive
  names remain deployment-neutral and second merge is idempotent.
- Security: checksums no longer substitute for trust. Import cannot address code,
  config outside the explicit carried policy, absolute/parent/drive/backslash paths,
  duplicate aliases or symlink escapes from the configured target root.
- Reliability: integrity failure happens before writes; dry-run is side-effect free;
  pipeline/artifact locks still protect real mutation; legacy schema-1 bundles are
  readable without rolling runtime contracts backward.
- Deployment: Kubernetes keeps the image ENTRYPOINT, one writer and shared volumes;
  teardown preserves the PVC; maintenance runs the same image and backup state.
- Coverage: direct bootstrap smoke was intentionally not run because it recursively
  replaces `workspace/src`; its isolated Python coverage plus the real entrypoint and
  state CLI provide safe evidence without risking user clones.

## Validation

- Real CLI: export → checksum inspect → write-free dry-run → relocated import →
  idempotent re-import; registry, catalog and knowledge landed under receiving state.
- `tests/entrypoint-smoke.sh`: 17/17 passed.
- Focused deployment/bootstrap/portability suite: 77 passed.
- Windows large-body regression: 5 consecutive passes; full adversarial API: 104
  passed.
- Repository-configured Ruff, Python compilation and `git diff --check`: passed.
- Full `registry/tests`: 1,700 passed in 726.55 seconds.

## Residual risk

- Integrity preflight prevents archive-driven partial restores, but a host crash or
  disk-full error during the subsequent multi-file write can still leave a partial
  estate. Operators should retain the source bundle and rerun replace after recovery.
- Docker/Podman and a Kubernetes/OpenShift client were unavailable, so manifests were
  parsed and cross-checked rather than deployed to a live cluster.
- The real bootstrap smoke recursively replaces `workspace/src`; it was not run
  against a workspace containing user clones. Its isolated stage tests passed.
