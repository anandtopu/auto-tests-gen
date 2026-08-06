# Per-file Analysis: A2 PR + JIRA Context Fusion Plan

Date: 2026-08-06

| File | Responsibility | Local Status | Findings | Test/Validation Gap | Action |
|---|---|---|---|---|---|
| `docs/prd-pr-jira-fused-context-multi-agent.md` | A2 requirements and rollout | Needs clarification | A2.5 baseline ambiguous after A1 | No explicit baseline fixture | Pin to A1 commit behavior |
| `engine/lib/ticket_discovery.py` | Candidate extraction/selection/provenance | Issue | Selection trusts validation rows; response identity is external | Wrong-key successful response absent | Add identity operation/test |
| `engine/pipeline.sh` | Port calls, materialization, phase wiring | Issue | Selected file is noncanonical; guidance is non-PR-only; ticket tail is discovery-only | No A2 behavioral pipeline test | Implement WP1/WP2/WP4 |
| `engine/lib/ticket_fields.py` | One safe parse into shell fields | Extend | Correct reusable seam; guidance policy remains shell-side | No security-label precedence output | Emit/pin guidance kind |
| `engine/lib/context_scope.py` | Scoped estate selection and budget | Issue | Reads ticket as signal only; cannot keep AC content | No ticket-budget tests | Add manifest-aware tail budget support |
| `engine/phases/run_phase.sh` | Cache-ordered prompt assembly | OK/constraint | File order is the cache contract; later arguments are later context | No A2 ordering pin | Keep unchanged; test caller order |
| `prompts/issue-types/*.md` | Story/bug/security authoring guidance | OK | Existing content is reusable | PR path does not receive it | Reuse unchanged |
| `engine/lib/run_record.py` | Durable run evidence | Extend | A1 provenance exists; fused/omitted context evidence absent | Explainability gap for content seen | Snapshot ticket-context manifest |
| `registry/org-config.yaml` | Scope policy/budget | OK | Triage scoped, generate deliberately unscoped | A2 must handle both paths | No new knob; use existing policy |
| `registry/tests/test_ticket_discovery.py` | A1 policy/ports/provenance | Extend | Strong state coverage; response identity missing | Malformed/mismatched success | Add WP1 cases |
| `registry/tests/test_ticket_fields.py` | Parse parity/eval safety | Extend | Existing injection pin is valuable | Guidance classifier absent | Add precedence/parity cases |
| `registry/tests/test_context_scope.py` | MUST-KEEP/budget/determinism | Extend | Estate chunks covered | Ticket AC/prose absent | Add tiny-budget and manifest cases |
| `registry/tests/test_p0_features.py` | Issue-guidance existence/policy | Extend | Prompt files pinned | PR/JIRA selection parity absent | Add shared-path assertions |

## Notes

- No new persistence, migration, route, or adapter verb is needed for A2.
- `run_phase.sh` should remain untouched unless implementation evidence proves
  its general cache-order contract insufficient; caller argument order is the
  narrower change.
- The dedicated plan treats hostile ticket text and stale scratch artifacts as
  first-class adversarial cases.
