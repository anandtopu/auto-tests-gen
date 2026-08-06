# A4 Near-Duplicate Detection — Action Plan

Date: 2026-08-06

All actionable A4 findings from the per-file and integration passes were fixed
in this iteration. No open A4 code action remains.

| Follow-up | Owner / backlog | Status |
| --- | --- | --- |
| Establish labelled retrieval fixtures and report precision@5, recall@5, and MRR before threshold tuning/default enablement | A5 | Next eligible backlog item |
| Decide whether near-duplicate detection stays permanently advisory | PRD D5 / QE Lead | Product decision; the current invariant remains advisory |

Completion gate: focused and broad tests green, staged diff check clean, commit
pushed to the configured upstream. The final SHA and validation totals are added
to the automation run report rather than hard-coded before validation completes.
