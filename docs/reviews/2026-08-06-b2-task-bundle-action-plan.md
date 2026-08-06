# B2 Task Bundle — Review Action Plan

## Release gate

No open P0–P2 B2 finding remains. Focused Ruff, the 98-test B2 matrix, and the
1,384-test registry compatibility suite pass. Commit eligibility now requires the
cached-diff check, exact-file staging, push, and upstream verification.

## Completed actions

| ID | Priority | Action | Acceptance check |
| --- | --- | --- | --- |
| B2-R1 | P1 | Isolate and owner-bind capture journals. | Two interleaved runs finalize disjoint phase manifests. **Done.** |
| B2-R2 | P1 | Bind historical bundles to run record identity. | A valid bundle for another run is rejected; live scratch is not borrowed. **Done.** |
| B2-R3 | P2 | Make fallback/unavailable states evidence-backed. | Missing, skipped, full-estate, failed capture, and disabled cases are distinct. **Done.** |
| B2-R4 | P2 | Recover transient owner-marker unlink failures safely. | Focused regression plus five repeated concurrent writer tests pass. **Done.** |
| B2-R5 | P2 | Carry full audit state without contaminating knowledge export. | Full round trip restores configured store; knowledge profile contains no B1/B2 store member. **Done.** |

## Next backlog

- B3: conservative cross-task reuse with generator/input identity, denylisted
  side-effecting phases, and disjoint savings attribution.
