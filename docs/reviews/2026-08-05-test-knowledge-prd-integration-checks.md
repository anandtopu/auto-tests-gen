# Cross-file Integration Checks: Test Knowledge Base PRD v2

Date: 2026-08-05

## Flow Checks

| Flow | Files Checked | Status | Evidence | Gap/Risk | Action |
| --- | --- | --- | --- | --- | --- |
| Test files → chunks → vectors → context | `testcase_parser.py`, `knowledge_chunks.py`, `vector_index.py`, `context_scope.py`, `spec_exemplars.py` | Pass for local roots | Case chunks, SHA skip, consumer switch, and data framing are pinned | Estate completeness waits for A2 | Add checkout coordinator. |
| Registry → checkout → estate index | registry, SCM adapters, `guidance_sync.py`, chunk builder | Fail | Builder selects workspace/demo only | Missing repo silently disappears | A2 clone coordinator and explicit degradation. |
| PR/JIRA → impact candidates → generate → explain | `pipeline.sh`, `extend_scout.py`, run record, `explain.py` | Partial | PR route join exists | JIRA, symbols, semantic mode, archive missing | A3 versioned artifact on both paths. |
| Proposed scenario → duplicate warning → human outcome | plan/selection/review/comment flows | Fail | No duplicate detector/outcome | PR has no plan editor | Mode-specific A4 checkpoints. |
| Generated test → gate commit → index | gate/pipeline/chunk refresh | Fail | Nightly/later-run rebuild only | Latest generated code not retrievable | A6.1 same-run upsert. |
| Generated context → durable blob → run bundle → explain/export | context, explain, state bundle | Fail | Scratch manifests expire | B1 metadata model conflicts with dedup | Blob/reference store, then B2 bundles. |

## Contract Checks

| Contract | Producer | Consumer | Status | Notes |
| --- | --- | --- | --- | --- |
| Chunk fields/kinds | `knowledge_chunks` | vector/context/exemplars | Partial | Add `testcase`, metadata, and logical case/part identity. |
| Impact candidates | A3 | prompts, comments, explain | Missing | Version schema before pipeline hooks. |
| Artifact blob/reference | B1 | B2/B3/explain/export | Missing | Separate immutable content from provenance. |
| Review outcome provenance | selection/review | A6 ranking/M6 | Missing | Append-only, case-ID linked. |
