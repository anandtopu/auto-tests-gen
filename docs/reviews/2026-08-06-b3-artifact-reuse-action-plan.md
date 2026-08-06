# B3 Artifact Reuse — Review Action Plan

## Release gate

No open P0–P2 B3 finding remains. Focused Ruff, the 106-test B3 matrix, and the
1,398-test registry compatibility suite pass. Commit eligibility now requires
cached-diff validation, exact-file staging, push, and upstream verification.

## Completed actions

| ID | Priority | Action | Acceptance check |
| --- | --- | --- | --- |
| B3-R1 | P1 | Key durable reuse on complete input and generator identity. | One input byte, model/policy, or generator-version change misses. **Done.** |
| B3-R2 | P1 | Enforce one savings owner. | Phase-cache hit records zero B3 reuse; durable lookup is ordered after it. **Done.** |
| B3-R3 | P1 | Deny side-effecting phases and confine products. | Generate/validate plus traversal/undeclared/link attacks cannot restore files. **Done.** |
| B3-R4 | P2 | Report honest, durable attribution. | Run record, cost, and explain show outcome/reason and reported/estimated tokens without artifact dollars. **Done.** |
| B3-R5 | P2 | Preserve default-off and deployment behavior. | Disabled mode writes nothing; relocated full-state product round trip passes. **Done.** |

## Next backlog

- B4: opt-in structured facts for application repositories without changing the
  existing guidance generator or precedence.
