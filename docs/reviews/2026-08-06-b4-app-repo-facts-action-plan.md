# B4 Application-Repository Facts — Review Action Plan

## Release gate

No open P0–P2 B4 finding remains. Focused Ruff, 35 focused tests, 83 integration
tests, and the 1,409-test registry compatibility suite pass. Commit eligibility
requires cached-diff validation, exact-file staging, push, and upstream
verification.

## Completed actions

| ID | Priority | Action | Acceptance check |
| --- | --- | --- | --- |
| B4-R1 | P1 | Make app participation presence-based and preserve no-file parity. | No authored file yields no app facts and follows the legacy guidance harvest/create-once path. **Done.** |
| B4-R2 | P1 | Refresh opted-in facts through the existing generator without changing authority. | Existing generated scratch refreshes; repo-owned guidance still wins and no second generator exists. **Done.** |
| B4-R3 | P2 | Harvest deterministic registry, surface, ownership, and catalog evidence. | Two app rebuilds are byte-identical; backend/frontend fixtures and no-model/subprocess pins pass. **Done.** |
| B4-R4 | P2 | Keep unavailable evidence truthful and optional input total. | Missing, empty, not-configured, malformed, and damaged cases are separately handled and tested. **Done.** |
| B4-R5 | P2 | Preserve durable/authored and rebuildable/derived deployment semantics. | State-bundle, relocation, gitignore, and broad compatibility tests pass. **Done.** |

## Follow-up backlog

- None in this PRD. A1–A6 and B1–B4 are implemented. The optional structured
  facts UI editor remains a separate product decision, not an incomplete B4
  acceptance criterion.
