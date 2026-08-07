# A3 - Plan-first from PR implementation plan

Date: 2026-08-07
Status: Implemented
Source: [prd-pr-jira-fused-context-multi-agent.md](prd-pr-jira-fused-context-multi-agent.md)

## Acceptance mapping

| Criterion | Implementation | Verification |
| --- | --- | --- |
| A3.1 one lifecycle | PR plans use key `PR-<repo>-<number>` in the existing `plan_state` entry. Only resume/delivery target metadata is added; draft, approval, edit revocation, signed spec, adversary/arbiter, and `tests` resume are unchanged. | Lifecycle, target-integrity, edit-revocation, and approval-refusal tests. |
| A3.2 requester surfaces | Plan authoring always comments on the PR and also comments on the validated discovered ticket. Resume retains the stored ticket for best-effort delivery even if later discovery is unavailable. | Mock pipeline asserts both adapter ports receive the draft notice. |
| A3.3 no plan run record | PR plan mode exits at the same pre-generation draft boundary as ticket plan mode. | End-to-end before/after run-record inventory assertion. |
| A3.4 wizard and queue | Queue mode `plan` accepts a repository plus PR number, derives the PR key, deduplicates it, supports PR URLs, and exposes plan/approve/generate controls in Guided run. | Queue, static surface, API-mode, and wizard-state pins. |
| A3.5 requirements decision | `require_requirements(..., pr_target=True)` returns an explicit exemption and the pipeline prints its rationale. Ticket plans remain gated. | Gate-on PR exemption and adjacent ticket-refusal adversarial test. |
| A3.6 spec decision | `spec_check` explicitly documents and enforces an approved structured PR spec; unsigned/free-form PR keys remain exempt. | Direct signed-uncovered refusal and unsigned-exemption tests. |

## Design and rollout

- `AIQE_PR_PLAN=0` preserves the historical PR and ticket paths. Both queue
  intake and direct pipeline entry fail closed until the flag is enabled.
- Invocation is `AIQE_PR_PLAN=1 bash engine/pipeline.sh plan <repo> <pr>`.
  The diff is the change authority; A1/A2's validated fused ticket and issue
  guidance enrich the analysis when available.
- Approval remains a content signature. The dashboard's Generate action queues
  the existing `tests <KEY>` mode, which reads the target from `plan_state`,
  re-fetches the PR, runs normal generation/review/gates, and reports to the PR.
- No migration or parallel store is introduced. Existing ticket-plan records
  have no `target` member and retain their byte-for-byte behavior.

## Validation and review

Focused A3 coverage includes flag-off intake, queue identity/deduplication,
target integrity, lifecycle revocation, requirements exemption, signed/unsigned
spec behavior, dual-surface comments, and the no-run-record invariant. Adjacent
plan, spec, requirements, wizard, and dashboard suites plus the full repository
review gate provide compatibility evidence. The combined focused/adjacent run
passed 89 tests, followed by 31 post-review lifecycle/wizard tests. Final
`make review` passed all 1,573 registry tests with 70.17% branch coverage
against the 67% floor, plus every adapter, gate/provider/state/routing/
observability adversarial, bootstrap, entrypoint, replay, context, discovery,
retrieval, reviewer, and scorecard stage. Two-pass review reports record the
per-file and cross-file findings and fixes.
