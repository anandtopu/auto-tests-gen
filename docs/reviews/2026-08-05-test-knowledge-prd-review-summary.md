# Review Summary: Test Knowledge Base PRD v2

Date: 2026-08-05
Reviewer: Codex
Branch/Commit: `main` / `28ad0e0`

## Overall Status

| Area | Status | Notes |
| --- | --- | --- |
| Per-file pass | Completed | PRD and directly named implementation modules reviewed. |
| Cross-file integration pass | Completed | Chunk/index/context, pipeline/gate/review, artifact/state flows traced. |
| Tests/build checks | Completed for checkpoint | 113 related tests passed; enabled index smoke passed. |
| Release readiness | Not ready | PRD is a six-slice backlog; only A1 has started. |

## Findings

| ID | Severity | Status | Owner Area | Summary | Evidence | Recommended Action |
| --- | --- | --- | --- | --- | --- | --- |
| TKB-001 | P1 | Resolved in plan | Artifact store | B1 puts run-specific provenance inside content-addressed deduplicated objects. | PRD B1.1–B1.2 | Split immutable blobs from append-only run references. |
| TKB-002 | P1 | Resolved in plan | Duplicate workflow | A4 says “before generation” and “plan editor” for a PR path that has no PR plan. | PRD §3.2, A4.1 | Use pre-generation JIRA and post-generation/pre-report PR advisory checkpoints. |
| TKB-003 | P2 | Open | Measurement | The unversioned baseline has already drifted. | PRD §4.1 says 28 chunks/13,318 chars; current read is 30/15,036. | Stamp baselines with commit, corpus hash, mode, and time; refresh before S2. |
| TKB-004 | P2 | In progress | Indexing | The A1 ID shape does not define duplicate titles or multi-part long cases. | PRD A1 shape and A1.4 | Deterministic collision suffix and shared logical `case_id`. |
| TKB-005 | P2 | Planned | Estate indexing | A1 “every repo” cannot be accepted independently of A2, and SCM has no tree-list verb. | PRD A1.1/A2.1; current `guidance_sync` known-file fetch | Deliver A1+A2 in S1 using read-only clone coordination. |
| TKB-006 | P2 | Planned | Security/eval | “Provably not alter behavior” needs a defined attack oracle. | PRD A1.6 | Pin framing in A1 and add behavior mutation attack to S2/A5. |
| TKB-007 | P2 | Resolved in plan | Impact artifact | Enumerating `unaffected` across an estate is unbounded and not useful. | PRD A3 recommendation vocabulary | Persist scored candidates plus explicit no-candidate state only. |

## Completed Scope

- PRD reviewed against current implementation rather than its self-description.
- Per-story implementation tasks, dependencies, flags, tests, and release gates written.
- First item A1 moved to implementation; parser/chunk/stat foundation completed.

## Incomplete Or Deferred Scope

- D3–D7 still need their named human owners by the PRD deadlines.
- Real cost validation remains blocked on real-provider parity evidence.

## Next Actions

1. Complete A1 parser/chunk/stat implementation and focused tests.
2. Start A2 checkout coordination to make A1 estate-complete.
