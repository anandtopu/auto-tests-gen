# Review Action Register: JCTS-S5 comment idempotency

Date: 2026-08-08

| ID | Severity | Status | Owner Area | Finding | Evidence | Recommended Action | Validation Expected | Dependencies |
|---|---|---|---|---|---|---|---|---|
| JCTS-S5-01 | P1 | Completed | Reliability | Appending after an ambiguous PUT failure could create a duplicate if Jira applied the request before disconnecting | Update transport trace | Record `failed` and do not append unless the adapter returns a closed safe-fallback reason | Forced ambiguous failure has no `comment` call | S3 receipts |
| JCTS-S5-02 | P1 | Completed | Security/data integrity | Historical comment ids and timestamps were reused without a closed validation boundary | Prior-record lookup review | Restrict ids to Jira/mock-safe characters and timestamps to finite numbers before adapter use | Malformed records cannot become update URL arguments or break lookup | S3 records |
| JCTS-S5-03 | P2 | Completed | Idempotency | Global run-id replacement in digest input could erase an unrelated content difference containing the same text | Hash normalization review | Normalize only platform-owned run attribution fields | Supported legacy/rich retries match while arbitrary content remains significant | none |
| JCTS-S5-04 | P2 | Completed | Deployment/security | Jira update initially ignored `AIQE_SSL_VERIFY`; malformed author JSON did not emit a closed authorship state | Adapter/config cross-file pass | Share TLS flags and fail malformed identity as `authorship_unverified` before PUT | Bash syntax, TLS pin, owned/mismatch functional tests | Jira adapter |
| JCTS-S5-05 | P1 | Completed | Contract/integration | Adding a visible marker after S4 bounded its rendering could exceed the org/Jira comment limit | Renderer -> delivery trace | Re-bound the final decorated body, keep whole lines, and state truncation | 256-character bound includes a complete marker and truncation notice | S4 renderer |

## Status Summary

| Status | Count |
|---|---:|
| Open | 0 |
| In Progress | 0 |
| Blocked | 0 |
| Ready for Verification | 0 |
| Completed | 5 |
| Deferred | 0 |
