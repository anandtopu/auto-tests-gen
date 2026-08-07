# A1.6 Terminal Ticket Status — Per-File Review

## Scope

Branch `codex/test-knowledge-a1-a2`; Tracker status adapter output, discovery
provenance, PR pipeline ordering, fused context, explain/comment surfaces,
benchmark fixture, and focused tests.

## Findings

| ID | Severity | File | Finding | Resolution |
|---|---|---|---|---|
| A1.6-R1 | P2 | `engine/lib/explain.py`, `engine/lib/pr_comment.py` | User-facing surfaces initially trusted the stored `terminal` boolean, allowing malformed historical evidence to manufacture a warning. | Added `recorded_selected_ticket()` to recompute state from bounded status/category fields; added forged-evidence test. |
| A1.6-R2 | P3 | `engine/lib/pr_comment.py`, `registry/tests/test_pr_comment.py` | Modified files retained legacy multi-import and E402 lint violations. | Split imports, placed local imports after path setup, and made the changed-file Ruff gate green. |

No open P0, P1, or P2 finding remains.

## Validation

- Focused A1.6 suite: 42 passed.
- Full registry suite: 1,451 passed in 743.25 seconds.
- Changed-file Ruff: passed.
- Git Bash syntax for `pipeline.sh` and `jira.sh`: passed.

## Open Questions

None for A1.6. A1.7 TaskEvent propagation remains a separate backlog item.
