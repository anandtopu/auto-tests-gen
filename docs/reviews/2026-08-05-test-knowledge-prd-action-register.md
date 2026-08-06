# Review Action Register: Test Knowledge Base PRD v2

Date: 2026-08-05

| ID | Severity | Status | Owner Area | Finding | Evidence | Recommended Action | Validation Expected | Dependencies |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| TKB-001 | P1 | Completed | Artifact store | Provenance conflicts with content dedup | B1.1–B1.2 | Blob/reference split | Two runs share one blob and retain two references | B1 |
| TKB-002 | P1 | Completed | Workflow | PR has no pre-generation plan editor | §3.2/A4.1 | Mode-specific advisory timing | PR/JIRA flow tests; warning never blocks | A4 |
| TKB-003 | P2 | Open | Measurement | Baseline drift | §4.1 vs current artifacts | Version-stamped baseline | Currency/hash check | S2 |
| TKB-004 | P2 | Completed | Indexing | Duplicate/long case identity undefined | A1/A1.4 | Logical case ID + collision/part fields | Collision and split tests passed | A1 |
| TKB-005 | P2 | Completed | Estate indexing | Missing repo is silently skipped | `index_checkouts.py` + indexed/not-indexed repo-surface outcome | SCM clone coordinator, explicit reason | Mixed-estate and unavailable-SCM tests passed | A2 |
| TKB-006 | P2 | Open | Security | A1.6 attack oracle undefined | A1.6 | Deterministic framing plus S2 mutation attack | Attack changes no tools/scope/gate | A1/A5 |
| TKB-007 | P2 | Completed | Impact contract | Unaffected output can be unbounded | A3 | Bounded scored candidates + explicit none | Artifact schema/size tests | A3 |

## Status Summary

| Status | Count |
| --- | ---: |
| Open | 2 |
| In Progress | 0 |
| Completed | 5 |
