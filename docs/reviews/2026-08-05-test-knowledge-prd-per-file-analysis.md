# Per-file Analysis: Test Knowledge Base PRD v2

Date: 2026-08-05

| File | Responsibility | Local Status | Findings | Test/Validation Gap | Action |
| --- | --- | --- | --- | --- | --- |
| `docs/prd-test-knowledge-base.md` | Product contract | Issue | B1 provenance/dedup conflict; A4 PR timing mismatch; stale baseline | No currency pin for §4.1 | Resolve in plan; version future baselines. |
| `engine/lib/knowledge_chunks.py` | Deterministic chunk corpus | A1 completed / A2 open | Testcase chunks/fallback/stats now exist; roots still depend on run history | Estate acquisition not yet implemented | Continue with A2. |
| `engine/lib/vector_index.py` | Changed-chunk embeddings and search | OK for A1 | Exact single-kind query; testcase consumers must choose new kind | No >50k trigger telemetry | Update consumer in A1; plan trigger in operations. |
| `engine/lib/context_scope.py` | Data-framed retrieval context | Partial | Test code is now explicitly DATA; behavior attack remains | No hostile testcase behavior fixture | Add S2 attack test. |
| `engine/lib/spec_exemplars.py` | Semantic exemplar selection | A1 completed | Selects testcase kind only under the new flag | Compatibility suite passed | None for A1. |
| `engine/lib/extend_scout.py` | PR deterministic surface join | Partial | PR-only and endpoint/route-only by design | No JIRA/symbol/semantic contract | Preserve as top A3 signal; wrap in impact analysis. |
| `engine/pipeline.sh` | Both workflow orchestration | Partial | PR scout hook exists; no JIRA impact hook or same-run index hook | No A3/A6 end-to-end tests | Add in S1/S3 at named lifecycle points. |
| `engine/lib/review_state.py` / `selection.py` | Human outcomes | Partial | No duplicate-linked testcase provenance | M6 cannot be computed | Extend in A4/A6. |
| `engine/lib/state_bundle.py` | Portable state profiles | Partial | Current knowledge index is correctly derived/excluded; durable B2 differs | No artifact bundle profile | Add B2 explicit include/exclude rules. |
| `engine/lib/app_paths.py` / `fs_lock.py` | Mutable placement and concurrency | Ready | Required primitives already exist | New store must join estate-isolation pins | Reuse unchanged in B1. |
| `Makefile` / Settings / `.env.example` | Operator contract | Needs change | No PRD flags or `index-stats` target | Target documentation pin will fail if omitted | Add with each slice. |
