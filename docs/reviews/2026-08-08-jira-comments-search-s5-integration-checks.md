# Cross-file Integration Checks: JCTS-S5 comment idempotency

Date: 2026-08-08

## Flow Checks

| Flow | Files Checked | Status | Evidence | Gap/Risk | Action |
|---|---|---|---|---|---|
| First comment -> receipt/event/state | `pipeline.sh`, `ticket_comment.py`, adapters, `plan_state.py`, `run_record.py` | Pass | Focused accounting and lifecycle suites | None | No action |
| Unchanged retry -> no Tracker call | renderers, `ticket_comment.py`, prior run/plan receipts | Pass | Hash-normalized retry test records `skipped_unchanged` | Run id appears in supported legacy and rich formats | Normalize only `Run:` and `AI-QE run <id>` fields |
| Changed retry -> safe PUT | receipt lookup, capability verb, Jira/mock adapters | Pass | Owned-author functional test and full mock retry | Real Jira permission varies by estate | Capability plus runtime fallback; document rollout |
| Forged marker/human author -> append | Jira GET-author guard, delivery fallback, receipts/events | Pass | Adversarial test proves no PUT and records `author_mismatch` | Marker spoofing | Marker never used as authority or remote lookup |
| Unsupported/permission/missing -> supersession | capability/update signals, `ticket_comment.post` | Pass | Closed fallback mapping and body assertion | Ambiguous network failure could duplicate if appended | Ambiguous failure remains `failed` with no append |
| Plan mode without run record | `ticket_comment.py`, `plan_state.py` | Pass | New fields round-trip beside plan | None | No synthetic run record |
| Deployment/config | examples, settings UI, Jira adapter TLS/proxy behavior | Pass | Settings/props/deployment suite and TLS source pin | Real account identity must be supplied | Empty config is safe append-only fallback |

## Contract Checks

| Contract | Producer | Consumer | Status | Notes |
|---|---|---|---|---|
| `posted|updated|skipped_unchanged|failed` | delivery boundary | run record, progress, explain, events | Pass | S3 outcome model is unchanged and extended with optional payload-free metadata |
| comment id + SHA-256 + marker | adapter/delivery | prior lookup | Pass | Plan state and run records preserve the same receipt shape |
| stable Jira author identity | operator config | Jira update adapter | Pass | Exact accountId/key/name only; display name rejected as authority |
| Tracker update capability | Jira/mock adapters | delivery boundary/conformance | Pass | Unsupported is explicit and produces a stated supersession |

## Integration Findings

- JCTS-S5-01 (P1, fixed): an ambiguous update failure must not be followed by an append because the PUT may have landed.
- JCTS-S5-02 (P1, fixed): unvalidated historical comment ids could become adapter URL arguments; ids and timestamps are now closed and finite.
- JCTS-S5-03 (P2, fixed): replacing the run id globally in hash input could hide a legitimate body change; normalization is limited to platform run fields.
- JCTS-S5-04 (P2, fixed): Jira update did not initially share the adapter's TLS policy and malformed author data lacked a closed failure state.
- JCTS-S5-05 (P1, fixed): appending the S5 marker after S4 rendering could exceed `comments.max_chars`; final delivery now reserves the footer and states any additional truncation.

No open P0-P2 integration finding remains. Real Jira rollout is the remaining
operational check; local proof uses functional curl stubs and the mock Tracker.
