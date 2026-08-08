# JCTS-S2 action register

| ID | Severity | Status | Area | Finding | Resolution | Evidence |
| --- | --- | --- | --- | --- | --- | --- |
| JCTS-S2-01 | P1 | Completed | API boundary | A malformed adapter item or dishonest page count could escape the expected search-error path. | Normalize every row and validate string/list types plus `returned <= total` and `returned == len(items)` before responding. | Adversarial adapter-envelope HTTP tests. |
| JCTS-S2-02 | P1 | Completed | Queue validation | Existing-key dedupe originally returned before validating new provenance input. | Normalize and bound metadata before acquiring the queue lock or applying dedupe. | Duplicate submission with malformed metadata is rejected. |
| JCTS-S2-03 | P1 | Completed | Runtime authority | Captured queue attributes could accidentally become stale pipeline input. | Keep them display-only and preserve the two-argument source/key runner contract. | Runner spy receives exactly `("jira", "PROJ-301")`. |
| JCTS-S2-04 | P2 | Completed | UI/API deployment | Client and server behavior changed without a shared schema bump. | Increment both schema constants to 3. | Source/render contract assertion and compatibility suite. |
| JCTS-S2-05 | P2 | Accepted | Bulk reliability | Sequential per-item submission can partially complete. | Preserve per-item validation, stop on failure, and tell the operator earlier items remain queued. | Exact failure-copy and bulk-loop tests. |
