# Review Action Register: JCTS-S4 rich JIRA comments

Date: 2026-08-08

| ID | Severity | Status | Owner Area | Finding | Evidence | Recommended Action | Validation Expected | Dependencies |
|---|---|---|---|---|---|---|---|---|
| JCTS-S4-01 | P1 | Completed | Cost/reporting | Early budget-refusal projection omitted already-incurred cost and its basis | Refusal trace bypasses ordinary delivery projection | Read live ledger into refusal projection; preserve each basis separately | Simulated refusal renders `~` and basis; mixed bases never sum | S3 accounting |
| JCTS-S4-02 | P2 | Completed | Reliability/observability | Rich render exceptions silently returned the legacy body | Facade caught broad exceptions without evidence | Emit only exception class on stderr; never emit body/error detail | Forced renderer exception returns fallback and exposes degradation | none |
| JCTS-S4-03 | P2 | Completed | Data safety | Control bytes and malformed historical contract types could break or spoof output | Projection assumed dictionaries; sanitizer covered only CR/LF | Normalize contract containers and replace all C0/DEL controls | Malformed/control fixture renders safely | none |
| JCTS-S4-04 | P2 | Completed | Plan truncation | Pathological small bounds could slice the minimal approval line | Final fallback used string slicing | Use a complete short action sentence and honest omitted count | 200-character key at 256-character bound remains complete | none |
| JCTS-S4-05 | P2 | Completed | Delivery truth | Clone failures lacked a named reason while quarantines had one | Ticket renderer fell through to a bare enum | Render `CLONE_FAILED` with known exit or explicit unavailable reason | Clone-failure fixture | none |

## Status Summary

| Status | Count |
|---|---:|
| Open | 0 |
| In Progress | 0 |
| Blocked | 0 |
| Ready for Verification | 0 |
| Completed | 5 |
| Deferred | 0 |
