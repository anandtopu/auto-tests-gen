# B6 Implementation Plan — Reviewer Evaluation by Attack

Date: 2026-08-06
Status: Implemented, validated, and reviewed
PRD: docs/prd-pr-jira-fused-context-multi-agent.md §5 B6
Branch: codex/test-knowledge-a1-a2

## Acceptance mapping

| Requirement | Implementation | Verification |
| --- | --- | --- |
| B6 reviewer is attacked, not inspected | `eval/reviewer_quality.py` executes versioned fixtures through the production `test_reviewer.normalize_repo_contract` boundary and requires the exact verdict/category plus grounded file, test, and evidence terms. | Mutation-sensitive missed-class, lucky-category, wrong-class, malformed-contract, and noisy-clean tests |
| B6.1 seeded defects | `eval/reviewer/v1/fixtures.json` contains one vacuous assertion, uncovered acceptance criterion, ticket contradiction, and convention breach. `labels.json` is QE-owned, fixes the M3 target at 100%, and SHA-pins fixture bytes. | Fixture ownership, hash drift, path confinement, threshold, coverage, and per-class catch-rate tests |
| B6.2 clean control | The fifth fixture is complete and expects `approve` with zero findings; any false positive fails the evaluator. | Clean pass and injected false-reject tests |
| B6.3 honest real-model comparison | The default result and scorecard label scripted output `SIMULATED` and state that it proves plumbing only. `make reviewer-eval-real` is an explicit, potentially billable call to the configured reviewer over the same fixtures. Blocked/unavailable authentication is recorded as unmeasured and returns nonzero. | Default CLI, injected measured-output, explicit provider-failure, Makefile, and scorecard tests |

## Implementation boundary

- Evaluation does not change the default-off reviewer flag, delivery policy,
  generated files, human review state, or gate outcome.
- Scripted contracts intentionally traverse the same closed verdict/category
  and text-boundary normalization as run evidence. They do not impersonate
  model output: the artifact-level `measurement_state`, command output, and
  scorecard all say `SIMULATED`.
- A result marked `simulated` is rejected from the real-model score even if all
  of its verdicts match, so injected mock output cannot be laundered into a
  measured quality claim.
- The real-model command is not a dependency of `make eval` or `make review`.
  It checks the configured provider and model, invokes only the read-only
  reviewer phase policy, extracts the same schema, reports provider/model/cost,
  and fails closed when the provider or authentication is unavailable.
- Fixture labels accept only a sibling file name, require exact fixture/label
  identity, cover every reviewer category exactly once plus one clean control,
  and reject any fixture edit until a QE lead deliberately re-pins the hash.
- B2 remains separate. The evaluator measures the B1 reviewer before bounded
  repair can alter findings or generated tests.

## Validation evidence

- Focused B6 suite: 16 passed.
- Related reviewer/evaluation/docs compatibility suite: 76 passed.
- Full registry compatibility suite before the final read-only-policy pin:
  1,511 passed in 718.52 seconds; the final focused/related suites cover that
  non-behavioral hardening change.
- Python compilation passed. Ruff was not installed in the repository virtual
  environment, so no Ruff result is claimed.
- The deterministic evaluator reports 4/4 seeded classes caught and the clean
  control approved; the result is labelled `SIMULATED`.
- Real-model quality remains unmeasured because current-head parity is blocked
  by expired Claude authentication. No billable call was attempted. Restore
  `claude login` or `ANTHROPIC_API_KEY`, then run `make reviewer-eval-real`.
- Broad compatibility and staged whitespace evidence are recorded in the B6
  review reports and iteration commit.

## Residual work

B2 now uses reviewer findings for a separately metered, bounded
repair/revalidate/rereview loop. B3 is next and decides when unresolved findings
can block delivery. The reviewer model tier remains provisional until
the real B6 fixture rate and clean-control result are measured.
