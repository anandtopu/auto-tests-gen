# Per-file Analysis: TCA-A2 Exit-path Coverage Proof

Date: 2026-08-08

| File | Responsibility | Local Status | Findings | Test/Validation Gap | Action |
|---|---|---|---|---|---|
| `eval/token_cost_coverage.py` | Isolated eight-path pipeline sweep and M1 evidence | OK | Uses current tracked contents and a disposable estate; asserts one entry, expected status, attribution, phase/basis semantics, and lock release | None after full sweep | Keep in `make eval` |
| `Makefile` | Evaluation entry point | OK | TCA proof now runs in the standard eval chain | None | None |
| `engine/phases/mock_phase.sh` | Controlled mock clarification and child termination | OK | Kill occurs after the provider-call start marker; controls cannot reach real adapters | Initial empty-placeholder cleanup used non-portable `sed -i` | Replaced with the existing Python path |
| `engine/pipeline.sh` | Phase status propagation to live accounting | OK | Every direct provider wrapper now passes child status to `budget.py record` | None after call-site search | Preserve all four call sites |
| `engine/lib/budget.py` | Live meter and mock simulation | OK | Simulation is suppressed only for a failed child without a provider result; successful simulation and real results retain prior behavior | Initial sweep exposed no-result failure promotion | Fixed and covered |
| `engine/lib/spend_ledger.py` | Durable basis classification | OK | Blank-basis rows become simulated only when actually metered; a started failed child becomes unrecorded | Initial sweep failed the mid-kill assertion | Fixed and covered |
| `registry/tests/test_token_cost_coverage.py` | Evaluator contract and adversarial controls | OK | Mutation-oriented pins reject false never-started rows, wrong basis, stale locks, and wrong attribution | None | None |
| `registry/tests/test_spend_ledger.py` | Direct failed-mock regression | OK | Pins unknown fields and unrecorded basis when simulation is configured but the child failed | None | None |

## Notes

- No evaluator artifact or generated plan/run state is written into the operator
  estate. The result JSON is under the existing ignored `eval/results/` path.
- The live `out/cost.tsv` remains the enforcement authority; TCA-A2 changes only
  whether a mock simulation may stand in for a failed call with no result.
