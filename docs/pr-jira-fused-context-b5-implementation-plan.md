# B5 - Reviewer cost containment implementation plan

Date: 2026-08-07
Status: Implemented
Source: [prd-pr-jira-fused-context-multi-agent.md](prd-pr-jira-fused-context-multi-agent.md)

## Acceptance mapping

| Criterion | Implementation | Verification |
| --- | --- | --- |
| B5.1 judgement tier | The `reviewer` model moves to the capable tier beside `reviewrepair`; neither phase appears in the degradation allow-list. | Model-policy and degradation source/behavior pins. |
| B5.2 envelope uplift | `budget.workflow_envelope` is the shared calculation for runtime enforcement and queue intake. Active PR/JIRA/tests review adds a provisional $0.75 planning allowance to the unchanged base envelope; plan-only and disabled/off review add none. Explicit per-run cost limits still win. | Policy matrix, effective-cap, precedence, and queue-warning tests. |
| B5.3 panel deferral | Org config records a disabled/deferred panel and a 90-day real-evidence trigger. The threshold remains unset until Product closes E4; no panel phase exists. | Closed config-shape and phase-inventory pins. |

## Design decisions

- The $0.75 is planning headroom, not measured or simulated spend. It covers the
  PRD's one initial review plus one default repair/rereview cycle; real parity
  data must replace it, and estates increasing `review.max_loops` must revisit it.
- Default behavior is unchanged: `review.enabled: false` under `warn` keeps
  the historical PR $1.50, JIRA $4.00, plan $1.00, and tests $3.00 caps.
- `require` reserves reviewer headroom even if a per-run variable says off;
  `off` reserves none even if a per-run variable says on.
- Queue intake uses the same effective-cap function as `budget.check` and
  explains base plus uplift; the warning remains informative and never refuses.
- A panel is not a hidden feature flag. Adoption requires an agreed threshold
  and one complete 90-day quarter of real reviewer/human findings; E4 remains
  the explicit product decision.

## Validation

Focused and adjacent checks passed 150 tests, followed by 43 post-hardening
budget tests. Fatal Python lint, YAML parsing, and shell syntax passed. Both
load-bearing pins were mutation-tested. Final system-Python verification passed
all 1,566 registry tests with 70.15% branch coverage against the 67% floor.
The exact adapter, gate/provider/state/routing/observability adversarial,
bootstrap, entrypoint, replay, context, discovery, retrieval, reviewer and
scorecard stages all passed in Git Bash. Commit, push, and remote parity
complete the iteration.
