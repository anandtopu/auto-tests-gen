# Review Summary: A2 PR + JIRA Context Fusion Plan

Date: 2026-08-06
Reviewer: Codex
Branch/Commit: `codex/test-knowledge-a1-a2` / `c9a4a3f` baseline

## Overall Status

| Area | Status | Notes |
|---|---|---|
| Per-file pass | Completed | PRD, A1 artifacts, pipeline, ticket parsing, scoping, phase assembly, configuration, and tests reviewed |
| Cross-file integration pass | Completed | Discovery-to-prompt, guidance parity, budget, cache order, fan-out, security, and rollback traced |
| Tests/build checks | Completed | Baseline focused tests and documentation checks recorded below |
| Release/demo readiness | Not Ready | A2 is planned, not implemented; open findings are implementation requirements |

## Findings

| ID | Severity | Status | Owner Area | Summary | Evidence | Recommended Action |
|---|---|---|---|---|---|---|
| A2-F1 | P1 | Open | Discovery/Tracker | Exit 0 does not prove the returned ticket key matches the candidate. | `engine/pipeline.sh:335` | Validate JSON identity before recording `valid`. |
| A2-F2 | P1 | Open | Pipeline | Selected data stops at `out/discovered-ticket.json`, while shared machinery consumes `out/ticket.json`. | `engine/pipeline.sh:356,391` | Atomically promote the already-fetched selected response. |
| A2-F3 | P1 | Open | Context scope | Ticket JSON is only a retrieval signal; ACs are not rendered or MUST-KEEP. | `engine/lib/context_scope.py:120,167` | Add budget-aware framed tail with mandatory ACs. |
| A2-F4 | P2 | Open | Guidance | Guidance selection exists only inside the non-PR branch. | `engine/pipeline.sh:408-413` | Emit shared guidance kind from the single ticket parse. |
| A2-F5 | P2 | Open | Prompt/security | Raw JSON fusion would not provide omission evidence or a dedicated data frame. | `engine/phases/run_phase.sh:93-104` | Render deterministic, bounded Markdown and append it at the run tail. |
| A2-F6 | P2 | Completed | Product/QA | A2.5 did not identify whether “today” means pre-A1 or the new A1 behavior. | PRD A1.5 and A2.5 | Plan pins parity to `c9a4a3f` and retains A1 no-ticket state. |

## Completed Scope

- Reviewed A2.1–A2.5 against current code rather than PRD assumptions.
- Produced a file-level work breakdown, data flow, acceptance map, adversarial
  test matrix, validation order, rollback, and completion gate.
- Resolved the A2.5 baseline ambiguity in the implementation plan.

## Incomplete Or Deferred Scope

- A2 code and tests remain intentionally unimplemented in this planning pass.
- Live provider validation is deferred to implementation/UAT; no network port
  contract change is planned.
- A3, A4, and Epic B remain outside this review.

## Validation Evidence

| Check | Result | Notes |
|---|---|---|
| PRD-to-code trace | Pass | All A2 criteria mapped to current producers/consumers |
| Git scope/upstream | Pass | Baseline synced; unrelated `AGENTS.md` change preserved |
| Focused baseline tests | Pass | 47 discovery/ticket-fields/context-scope/guidance/phase-cache tests |
| Markdown whitespace | Pass | Tracked diff and new review/plan files checked |

## Next Actions

1. Implement WP1 response identity validation and canonical materialization.
2. Implement WP2 shared guidance before introducing ticket prompt content.
3. Add WP3 budget/framing tests before wiring phase arguments.
