# PR/JIRA B5 Cost Containment - Per-File Review

Date: 2026-08-07
Branch: `codex/test-knowledge-a1-a2`
Scope: reviewer tier, effective workflow envelopes, queue warning, panel deferral.

## Pass 1 - file-by-file findings

| File(s) | Review result |
| --- | --- |
| `registry/org-config.yaml` | Reviewer and repair use the capable tier. Base caps remain intact, review uplift is explicitly provisional, and panel state is closed/deferred with no threshold masquerading as measured. |
| `engine/lib/budget.py` | One total helper applies reviewer-policy precedence and returns effective/base/uplift. Explicit environment cap still wins. Boolean-as-money coercion was found and fixed. |
| `engine/lib/work_queue.py` | Intake consumes the runtime helper rather than reparsing config, warns against the effective cap, explains base plus uplift, and never refuses. |
| `engine/phases/run_phase.sh` | Reviewer and reviewrepair remain outside the cheap-phase allow-list; shell syntax is unchanged except for the policy comment. |
| B5 tests | Policy, cap, precedence, malformed-money, warning, panel, and source pins cover the acceptance boundary. One wrong-key warning fixture was found and fixed before it could become false evidence. |
| Architecture, cost, UI, and user docs | Cap values, provisional evidence label, no-downgrade rule, warning behavior, and deferred trigger agree with code. |
| B5/master plans | Acceptance and delivery order map to repository evidence. |

## Findings fixed

- **B5-R1 (P2):** YAML booleans pass Python numeric type checks and could become
  a $1.00 cap or uplift. Strict non-boolean positive-number checks and an
  adversarial regression test now reject them.
- **B5-R2 (P2):** the first queue-boundary test used a JIRA-shaped history key
  while invoking PR mode, so it passed without exercising the comparison.
  The fixture now uses the exact `PR-orders-api-9` identity.
- **B5-R3 (P2):** `make review` reproducibly failed under system Python 3.14
  when Git/Git-Bash subprocess tests inherited an invalid Windows stdin handle,
  despite passing under the repository environment. The affected launch
  helpers now use `stdin=DEVNULL`, matching the repository's established
  cross-runtime convention.

No open P0-P2 per-file finding remains.

## Validation

- Focused B5 and adjacent budget group: 43 passed after hardening.
- Broader reviewer/budget/queue/model/docs group: 150 passed.
- Both model-tier and envelope pins were mutation-tested and failed against
  their intended regressions.
- The initial full gate reached the coverage floor but exposed B5-R3; all 62
  affected tests passed in isolation before the harness fix.
- Final system-Python suite: 1,566 passed; branch coverage 70.15% (floor 67%).
- All remaining `make review` adapter, adversarial, smoke, replay, quality and
  scorecard stages passed inside the repository-required Git Bash runtime.
