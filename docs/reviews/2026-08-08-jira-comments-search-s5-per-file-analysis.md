# Per-file Analysis: JCTS-S5 comment idempotency

Date: 2026-08-08

| File | Responsibility | Local Status | Findings | Test/Validation Gap | Action |
|---|---|---|---|---|---|
| `engine/lib/ticket_comment.py` | Stable markers, payload-free hashes, prior receipt lookup, skip/update/fallback delivery | OK after fixes | Unsafe prior ids/timestamps, global run-id replacement, ambiguous update fallback, and post-render length growth were found during review | Needed adversarial historical, transport, and marker-bound cases | Validate identifiers/timestamps, normalize only supported run fields, never append after an ambiguous PUT, and re-bound the decorated body |
| `engine/lib/plan_state.py` | Durable no-run plan comment provenance | OK | New idempotency fields initially needed explicit preservation | Covered by plan-state round trip | Pass hash, marker, supersession id and fallback reason through the closed receipt builder |
| `adapters/tracker/jira.sh` | Real Jira capability, author verification and update | OK after fixes | Update initially bypassed `AIQE_SSL_VERIFY`; malformed author JSON could emit an uncontrolled traceback | Real Jira remains an operational rollout check | Share TLS policy, emit closed authorship states, compare only accountId/key/name before PUT |
| `adapters/mock/tracker.sh`, `engine/lib/mock_tracker_comments.py` | Credential-free persistent comment/update fixture | OK | None | Needed state to survive pipeline scratch cleanup | Synthetic JSONL with platform author and post/update history |
| `adapters/conformance/test_adapters.sh` | Tracker port surface | OK | Mock Tracker was not previously listed as a conformance surface | Behavioral semantics remain in pytest | Require capability/update verbs on Jira and mock adapters |
| `.env.example`, `aiqe.properties.example`, `engine/lib/settings_store.py` | Platform-author trust-anchor configuration | OK | None | Default must remain empty/safe | Expose non-secret stable account identity consistently |
| `registry/tests/test_ticket_comment_idempotency.py` | Unit, adapter, adversarial and two-run proof | OK | Initial retry assertion incorrectly expected skip despite a changed gate SHA | Corrected to assert in-place update; unchanged unit case remains separate | 14 focused cases cover A3 and M2 |
| `docs/user-guide.md`, `docs/architecture.md`, `docs/integrations/jira.md` | Operator behavior, boundary and rollout contract | OK | None | Real Jira account-id selection remains operational | Document trust anchor, fallbacks, ambiguous failure behavior and port verbs |
| PRD plan and review registers | Acceptance/status evidence | OK | None | Final broad PRD verification remains JCTS-FINAL | Mark S5 only after validation evidence |

## Notes

- `AGENTS.md` is a generated, unrelated local change and is excluded from S5.
- Receipts and events contain only hashes/ids/closed reasons, never comment bodies.
