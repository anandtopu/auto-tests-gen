# A5 Retrieval Quality — Action Plan

Date: 2026-08-06

| Priority | Action | Status | Evidence |
| --- | --- | --- | --- |
| P1 | Reject malformed label/corpus rows and unsafe fixture references. | Completed | Loader validation and traversal/drift tests. |
| P1 | Keep lexical fallback distinct from semantic quality. | Completed | Separate `measured`, `simulated`, and `unmeasured` states and tests. |
| P1 | Make configured embedding failures fail closed. | Completed | Outage/count/dimension/numeric validation. |
| P2 | Make hostile retrieval framing mutation-sensitive and gate-independent. | Completed | Attack fixture/oracle and weakened-preamble test. |
| P2 | Stamp baselines with immutable and temporal provenance. | Completed | Label/corpus hashes, source commit, evaluation timestamp. |
| P3 | Collect M9 from real QA participants before enabling S3. | Pending external measurement | `m9-baseline.json` defines cohort and survey record; current state is `unmeasured`. |

Exit criterion for A5: focused and broad tests pass, `make eval` equivalent
components report no regression, cached diff passes whitespace checks, and the
iteration is committed and pushed. M9 collection remains a documented S3
precondition, not a fabricated A5 result.

Validation complete: 44 focused tests and 1,333 full registry tests passed. Git
diff/staging checks, commit, and push remain the final iteration gates.
