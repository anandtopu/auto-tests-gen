# A1.6 Terminal Ticket Status — Cross-File Integration Review

## Scope

Trace: JIRA response → bounded validation → selected discovery annotation →
authoring context → run record → explain/PR comment.

## Findings

| Dimension | Result |
|---|---|
| Correctness | Status is copied from the same identity-validated response; no refetch or second selection path exists. |
| Security | Status is bounded and blockquoted or Markdown-sanitized. Terminal state is recomputed before user-facing rendering. |
| Reliability | Missing provider status becomes explicit `unavailable`; wrong-key annotation fails closed; artifact cleanup remains before flag evaluation. |
| Product behavior | Closed/Done warns everywhere a selected ticket is surfaced but preserves `outcome: selected` and never changes routing or gate decisions. |
| Compatibility | Existing status-less ticket JSON remains valid. Flag-off and no-selection paths create no new calls or artifacts. |
| Deployment | Additive adapter fields only; no migration, service, dependency, port, or config change. Rollback remains `AIQE_PR_TICKET_CONTEXT=0`. |
| Coverage | Active, terminal, missing, malformed, custom JIRA category, wrong identity, forged evidence, live/historical comments, fused context, and real mock pipeline are covered. |

## Validation

Focused and full suites are green; no open P0/P1/P2 findings.

## Open Questions

Operational estates may use custom terminal names outside JIRA's `done`
category. The category handles standard JIRA customization; widening names
without provider evidence would create false warnings and is not recommended.
