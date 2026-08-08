# Cross-file Integration Checks: JCTS-S4 rich JIRA comments

Date: 2026-08-08

## Flow Checks

| Flow | Files Checked | Status | Evidence | Gap/Risk | Action |
|---|---|---|---|---|---|
| Structured plan -> canonical spec -> bounded ticket body -> Tracker receipt | `spec_store.py`, `ticket_comment_render.py`, `pipeline.sh`, `ticket_comment.py` | Pass | Mock plan lists three scenarios, marks arbiter additions and records posted receipt | Renderer degradation must not masquerade as rich success | Credential-free stderr degradation signal plus exact fallback |
| Generate/validate/gate -> one projection -> PR and ticket renderers | `pr_comment.py`, `run_record.py`, `pipeline.sh` | Pass | One projection object drives both renderers; live/replay PR parity and 75-test set pass | Parallel composition drift | Projection identity pin; no second delivery assembler |
| Fused PR -> validated selected ticket -> rich delivery -> run record | `ticket_discovery.py`, `pipeline.sh`, `ticket_comment.py`, `run_record.py` | Pass | Full mock PR comments PROJ-301, names orders-api#201, and persists its receipt before record assembly | Cached discovery attributes replacing runtime truth | Existing `Tracker get_item` flow unchanged; no queue metadata enters execution |
| Reviewer/budget refusal -> named reason/fix -> ticket | `pipeline.sh`, `ticket_comment_render.py`, `pr_comment.py` | Pass | Source-order, refusal and simulated-cost tests | Early budget path has partial artifacts | Dedicated refusal projection uses live ledger without inventing delivery artifacts |
| Org config/env/settings -> runtime flag and bound | `org-config.yaml`, examples, `settings_store.py`, `ticket_comment_render.py` | Pass | Default-off, invalid bound, ceiling and settings compatibility tests | Config drift | Single flag name and defensive 8,000 fallback |

## Contract Checks

| Contract | Producer | Consumer | Status | Notes |
|---|---|---|---|---|
| Structured scenario fields and `adversary_added` | `spec_store.merge_fold` | `spec_store.render_comment` | Pass | Marker is based on set difference at the arbiter boundary, never inferred from prose |
| Delivery projection | `pr_comment.delivery_projection` | `render_pr`, `render_ticket` | Pass | Tests, validation, gate, review, critic, discovery and cost bases share one schema |
| Ticket body | `ticket_comment_render` | `ticket_comment.post` -> Tracker adapter | Pass | Plain text, bounded, control-sanitized; no body enters receipts/events |
| Comment receipt | Tracker adapter / `ticket_comment` | run record, plan state, progress/explain | Pass | S3 model unchanged; new fused delivery is recorded before run-record assembly |
| Feature rollout | examples/settings/environment | `env_flag.flag` | Pass | Default `0`; only fused PR adds a new ticket post and only while enabled |

## Integration Findings

- P1 fixed: early refusal initially lost cost context; live ledger bases now flow
  into the refusal projection without blending.
- P2 fixed: rich rendering failure initially fell back silently; it now emits a
  bounded class-only degradation warning and preserves best-effort delivery.
- P2 fixed: malformed contracts/control characters and clone failures now render
  explicit, safe states instead of breaking or visually rewriting the comment.
- P2 fixed: the pathological small-bound plan fallback no longer slices a line.
- No open P0-P2 integration finding remains. The all-registry pytest command
  timed out at 20 minutes without a result; bounded broad verification passed.
