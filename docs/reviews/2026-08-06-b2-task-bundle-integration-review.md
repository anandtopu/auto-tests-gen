# B2 Task Bundle — Cross-File Integration Review

## Boundaries reviewed

| Boundary | Result |
| --- | --- |
| Phase producer → B1 store → task manifest | Exact prompt/input paths are captured before each initial, retry, and advisory provider call; manifests contain hashes and references, not bodies. |
| Run record → historical explain | Pointer, B1 reference, blob hash, schema, run ID, and key are verified. Deleted scratch works; corrupt or cross-run evidence is refused without substitution. |
| State export/import → configured placement | Full export holds the store lock and carries blobs/references; knowledge export excludes them; import validates paths/checksums and restores through `app_paths`. |
| Security | Existing B1 size, kind, secret, owned-guidance, and integrity policy applies to every captured byte. Logical paths never expose an operator's absolute layout. |
| Reliability | Per-run journals, atomic writes, lock-protected manifest assembly, explicit failure states, and the Windows owner-marker retry prevent mixing, tearing, and silent omission. |
| Deployment | The feature remains default-off. No provider/gate behavior changes when disabled; the store resolves to the durable reports/state volume when enabled. |
| Coverage | Unit, integration, adversarial, profile, corruption, relocation, pipeline-structure, and compatibility tests cover the acceptance map. |

## Findings fixed during integration pass

- B2-I1 (P1): historical explain previously trusted pointer integrity without
  binding it to the selected run record. Run/key binding is now mandatory.
- B2-I2 (P1): concurrent captures shared one journal. Journals are now run-isolated.
- B2-I3 (P2): export/import and B1 mutation needed one lock boundary; both portable
  operations now serialize with the store.
- B2-I4 (P2): full-estate fallback truthfulness now depends on successful capture.

No open P0–P2 B2 finding remains after the fixes and focused validation.
