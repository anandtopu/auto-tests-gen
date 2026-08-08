# Review Action Register: TCA-C1 Per-task Cost Statement

Date: 2026-08-08

| ID | Severity | Status | Owner Area | Finding | Evidence | Recommended Action | Validation Expected | Dependencies |
|---|---|---|---|---|---|---|---|---|
| TCA-C1-R1 | P1 | Completed | Accounting | Missing numeric amount on a reported/estimated/simulated row was coerced to zero | Cross-file totals review | Count structurally incomplete priced rows separately | Focused malformed-price test | TCA-A3 |
| TCA-C1-R2 | P1 | Completed | Isolation/deployment | Statement export path wrote into operator estate during tests | First broad run: isolation pin failed after 1,733 passes | Add centralized `AIQE_EXPORTS_DIR`, state-root resolution and test redirect | Isolation/path suite and full rerun | none |
| TCA-C1-R3 | P2 | Completed | Performance | Dashboard reread full durable history for every key | Render data-flow review | Supply one union snapshot to every statement panel | Snapshot no-reload test + dashboard render | TCA-A3 |
| TCA-C1-R4 | P2 | Completed | UX | `artifacts` printed the entire all-run statement before the artifact | Real CLI exercise produced 225 lines | Show compact basis summary and drill-down command | QA CLI regression | none |
| TCA-C1-R5 | P2 | Completed | Export security | Spreadsheet/Markdown control characters could alter rendered line items | Security checklist | Neutralize formula prefixes and flatten Markdown cells | CSV/Markdown adversarial test | none |
| TCA-C1-R6 | P3 | Completed | Test quality | New test imports/check mode were lint-inconsistent | Ruff pass 1 | Sort imports, remove stale noqa, make check mode explicit | Ruff clean | none |

## Status Summary

| Status | Count |
|---|---:|
| Open | 0 |
| In Progress | 0 |
| Blocked | 0 |
| Ready for Verification | 0 |
| Completed | 6 |
| Deferred | 0 |
