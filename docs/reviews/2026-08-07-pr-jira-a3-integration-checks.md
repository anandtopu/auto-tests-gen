# A3 Plan-first from PR - Pass 2 integration review

Date: 2026-08-07

| Dimension | Evidence | Result |
| --- | --- | --- |
| Correctness | Queue/API/wizard produce `plan <repo> <pr>`; pipeline derives `PR-<repo>-<pr>`; `plan_state.target` resumes through existing `tests`; generated run returns to PR delivery. | Pass |
| Lifecycle integrity | One state entry owns draft, approval signature, edit revocation, signed spec, generation provenance, and adversary/arbiter evidence. | Pass |
| Context authority | `out/pr.diff` and changed files drive analysis; A1/A2's validated ticket/guidance enrich the plan without replacing the diff. | Pass |
| Security | Target key and positive PR number are bounded at queue, direct pipeline, and persisted-state boundaries; dashboard mode/key and PR URL parsing remain validated; ticket text stays adapter data. | Pass |
| Reliability | Stored ticket survives later discovery unavailability for notifications; adapter comments remain best-effort; draft state persists independently; plan-only mode writes no run record. | Pass |
| Governance | PR requirements exemption is explicit and refusal-free; signed PR specs are deliberately enforced; unsigned PR runs retain exemption. | Pass |
| Deployment/default parity | Feature flag is default-off in both config formats and Settings; queue and wizard controls are hidden and backend intake refuses while off. No schema migration is required. | Pass |
| Coverage | 89 focused/adjacent tests and 31 post-review lifecycle/wizard tests passed. Full review: 1,573 registry tests, 70.17% branch coverage (67% floor), and all adversarial/conformance/evaluation stages. | Pass |

Residual risk: real SCM/JIRA/model parity was not exercised in this local mock
iteration. Existing adapter conformance and the broad repository review gate
cover mechanics; rollout remains opt-in until an estate validates credentials,
comments, and plan quality on representative PRs.
