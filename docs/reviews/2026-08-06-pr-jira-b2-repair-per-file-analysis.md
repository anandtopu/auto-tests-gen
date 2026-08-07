# B2 Bounded Repair — Per-File Analysis

Date: 2026-08-06
Scope: PRD v2 B2 bounded findings-driven repair
Branch: `codex/test-knowledge-a1-a2`

## Files reviewed

| File | Review result |
| --- | --- |
| `engine/lib/review_repair.py` | Loop cap, pending-repo selection, source confinement, before/after edit binding, strict contracts, apply-time revalidation, carry-forward logic, and Windows-safe writes reviewed. |
| `engine/lib/test_reviewer.py` | Nested history normalization, loop/order bounds, finding identities, repair/test evidence, unresolved survival, and surface compatibility reviewed. |
| `engine/pipeline.sh` | Initial review → bounded per-repo repair → validate → rereview ordering, unique labels, budget guards, failure semantics, and pre-gate placement reviewed. |
| `prompts/review-repair.md` | Untrusted-data framing, existing-file-only target, repo confinement, no test execution, and exact JSON output reviewed. |
| `engine/phases/contracts/reviewrepair.schema.json` | Required output fields reviewed; semantic constraints remain in the strict Python boundary. |
| `engine/phases/mock_phase.sh` | Physical edit side effect, empty repair, initial/final scripted verdicts, unique output labels, and malformed branch reviewed. |
| `engine/phases/run_phase.sh` | Authoring tier, non-degradation, unique contract output, and cache exclusion documentation reviewed. |
| `engine/lib/llm_runner.py`, `registry/org-config.yaml` | Agentic-provider enforcement, complete phase inventory, Sonnet authoring tier, and `Read,Edit` least privilege reviewed. |
| `engine/lib/phase_cache.py`, `engine/lib/artifact_reuse.py` | Write-enabled repair cannot be replayed from either cache. |
| `registry/tests/test_review_repair.py` | Default/custom/invalid caps; path, duplicate, evidence, no-op, repeated-finding, tamper, run-record, syntax, policy, and end-to-end mock cases reviewed. |
| `registry/tests/test_phase_cache.py`, `registry/tests/test_artifact_reuse.py` | Structural no-reuse regressions cover all workspace-editing phases. |
| `registry/tests/test_test_reviewer.py`, `registry/tests/test_llm_runner.py` | Unique reviewer labels and phase capability expectations updated without weakening B1 behavior. |
| `docs/architecture.md`, `docs/user-guide.md`, `docs/multi-llm-providers.md`, `CLAUDE.md` | Operator behavior, cost/consequence language, phase capabilities, and implementation invariants synchronized. |
| B2/master/B4/B6 implementation plans | Status and dependency handoff now identify B2 as implemented and B3 as next. |

## Findings fixed

| ID | Priority | Finding | Resolution |
| --- | --- | --- | --- |
| B2-R1 | P1 | A repair contract could claim a fix without proving that its file set matched the files actually edited. | Validation now compares embedded before-source with current source and requires the changed, fixed, and reported test sets to be identical; fictitious and omitted edits are adversarially tested. |
| B2-R2 | P1 | Persisted nested repair evidence was only shallow-validated, allowing malformed paths, fixes, tests, booleans, or oversized text to survive in a run record. | Every nested field is closed, bounded, path-checked, deduplicated, and re-normalized on load; tampered history becomes unavailable rather than trusted. |
| B2-R3 | P1 | `apply` trusted a prior validation step and did not revalidate the contract at the mutation boundary. | `apply_contract` now re-runs strict normalization against the current review and input before updating generation metadata. |
| B2-R4 | P1 | The new write-enabled phase was absent from the explicit durable-reuse denylist. | `reviewrepair` is denied alongside generate/validate in both cache layers, with structural regression tests. |
| B2-R5 | P2 | The first JSON writer used bare `os.replace`, violating the repository's Windows retry invariant. | All B2 JSON writes use `fs_lock.write_json_atomic`; the invariant test and focused B2 suite pass. |
| B2-R6 | P2 | The mock reviewer's malformed-output branch accidentally enclosed normal verdict selection, breaking shell control flow. | The malformed path is isolated behind an explicit `else`; Git Bash syntax and functional pipeline tests pass. |

No P0–P2 per-file finding remains open.

## Evidence

- Focused B2/durability: 23 passed.
- Related compatibility matrix: 225 passed.
- Full registry: 1,534 passed.
- Ruff fatal/error rules, Python compilation, and three shell syntax checks passed.
