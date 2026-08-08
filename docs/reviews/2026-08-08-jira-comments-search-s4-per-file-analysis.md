# Per-file Analysis: JCTS-S4 rich JIRA comments

Date: 2026-08-08

| File | Responsibility | Local Status | Findings | Test/Validation Gap | Action |
|---|---|---|---|---|---|
| `engine/lib/spec_store.py` | Canonical structured-plan and ticket-plan rendering | OK after fix | Whole-line truncation and deterministic arbiter provenance; initial tiny-bound fallback could cut an action line | Long-key and control-character boundaries | Replaced slicing with a complete minimal message; added adversarial tests |
| `engine/lib/pr_comment.py` | Shared delivery projection plus PR/ticket renderers | OK after fix | Initial early-refusal projection omitted cost; malformed contracts and control bytes needed normalization | Mixed bases, malformed history, clone failure | Added basis-preserving refusal cost, defensive normalization and sanitization |
| `engine/lib/ticket_comment_render.py` | Default-off facade, org bound, safe fallback | OK after fix | Initial renderer fallback was silent | Forced render exception | Emit credential-free degradation class and retain legacy body |
| `engine/pipeline.sh` | Plan/delivery/refusal routing and fused-ticket handoff | OK | All posts still cross `TICKET_COMMENT`; rich PR-to-ticket calls are flag/fusion gated and precede run-record assembly | Success, refusal and budget source ordering | Added helper wiring and mock fused/plan journeys |
| `engine/lib/settings_store.py`, `.env.example`, `aiqe.properties.example` | Rollout control exposure | OK | None | Default drift | All surfaces declare default `0`; settings tests included in broad set |
| `registry/org-config.yaml` | Organization comment length policy | OK | None | Invalid/oversize values | Default, invalid and Jira-ceiling cases tested |
| `registry/tests/test_ticket_rich_comments.py` | S4 acceptance and adversarial coverage | OK | None after mutation-oriented review | Real Jira formatting remains rollout validation, not local automation | 12 focused tests including two mock full journeys |
| `registry/tests/test_reviewer_surfaces.py`, `registry/tests/test_ticket_comment_accounting.py` | Existing ordering/accounting compatibility pins | OK | Literal helper name changed | None | Updated pins without weakening the ordering or boundary assertions |
| `docs/user-guide.md`, `docs/architecture.md` | Operator and system contract | OK | None | None | Documented flag, bound, projection, fallback and fused flow |

## Notes

- The generated `AGENTS.md` modification is unrelated and remains outside S4.
- Mock journeys rewrote tracked PROJ-301 artifacts; those test side effects were
  restored and are not part of the iteration.
