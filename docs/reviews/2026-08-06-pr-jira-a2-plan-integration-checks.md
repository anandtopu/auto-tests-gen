# Cross-file Integration Checks: A2 PR + JIRA Context Fusion Plan

Date: 2026-08-06

## Flow Checks

| Flow | Files Checked | Status | Evidence | Gap/Risk | Action |
|---|---|---|---|---|---|
| Tracker response → validated candidate | `pipeline.sh`, `ticket_discovery.py`, Tracker adapters | Partial | Exit states are closed | Successful wrong-key JSON is accepted | Validate response identity |
| Selected candidate → canonical ticket | `pipeline.sh`, `ticket_fields.py`, `context_scope.py` | Fail | Producer writes `discovered-ticket.json`; consumers expect `ticket.json` | Parallel schema/path or refetch temptation | Atomic canonical promotion |
| Ticket → issue guidance | `ticket_fields.py`, `pipeline.sh`, `prompts/issue-types/*` | Partial | JIRA semantics exist | PR branch bypasses them | Single classifier shared by both modes |
| Ticket → scoped triage context | `context_scope.py`, `run_phase.sh`, `pipeline.sh` | Fail | Ticket influences retrieval only | ACs can be absent from model input | Separate budget-aware tail, AC mandatory |
| Ticket → generation fan-out | `pipeline.sh` `GENERATE`, per-repo slices | Partial | A1 tail already fans out | Requirements/guidance absent | Append same fused tail after isolated repo context |
| Feature flag → rollback | settings/examples, `pipeline.sh` | Pass/extend | A1 full-block gating exists | A2 must not create stale artifacts on refusal | Empty arrays plus cleanup/pins |

## Contract Checks

| Contract | Producer | Consumer | Status | Notes |
|---|---|---|---|---|
| Selected ticket identity | Tracker response | A1 resolver/A2 fusion | Fail | Exit 0 alone is insufficient evidence |
| Canonical ticket JSON | A1 selected response | `ticket_fields`, scoping | Fail | Canonical path not produced in PR mode |
| Guidance kind | Ticket type/labels | issue prompt copy | Partial | Semantics correct but branch-local |
| Context budget manifest | `context_scope` | ticket renderer/run record | Partial | Existing used/budget evidence can be reused |
| Prompt file order | `pipeline.sh` | `run_phase.sh` | Pass/extend | Append fused file last; keep stable prefix first |
| No-selection states | A1 discovery | A2 arrays/files | Pass by design | Plan keeps every state closed and non-fused |

## Integration Findings

- **P1:** Fusion must be gated on response identity, not merely Tracker process
  success; otherwise a misbehaving proxy/fixture can feed a different ticket.
- **P1:** Budget correctness and prompt-cache ordering require two coordinated
  outputs: the existing estate context first and a manifest-accounted ticket
  file at the run-specific tail.
- **P2:** Sharing guidance through `ticket_fields.py` avoids PR/JIRA policy drift
  and a second ticket parse.
- **P2:** Multi-repo fan-out must share requirements while retaining existing
  per-repo conventions/catalog confinement.
