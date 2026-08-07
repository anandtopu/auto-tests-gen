# Fused PR + JIRA Context and Agent Review — Implementation Plan

Date: 2026-08-06
Source: [prd-pr-jira-fused-context-multi-agent.md](prd-pr-jira-fused-context-multi-agent.md)

## Delivery order and status

| Order | Item | Slice | Dependencies | Status | Implementation boundary |
| ---: | --- | --- | --- | --- | --- |
| 1 | A1 Ticket discovery | S1 | none | Implemented | Opt-in SCM metadata collection, earned-key extraction, Tracker validation, deterministic selection/refusal, terminal-status provenance/warnings, intake field, provenance, explain, and TaskEvent explicit-key parity |
| 2 | A2 Context fusion | S1 | A1 | Implemented | Exact response identity, canonical `out/ticket.json`, shared guidance selection, scoped prompt tail, mandatory acceptance-criteria retention, flag-off parity; evidence in [pr-jira-fused-context-a2-implementation-plan.md](pr-jira-fused-context-a2-implementation-plan.md) |
| 3 | A4 Discovery evaluation | S2 | A1 | Implemented | Hash-pinned QE labels, validation-aware per-signal precision/recall, correct-refusal accounting, simulated evidence labelling, and `make eval` integration |
| 4 | B1 Test reviewer | S3 | none; schedule after S2 | Implemented | Read-only per-repo pre-gate reviewer, strict contracts, deterministic skip, unavailable state, and simulated mock evidence |
| 5 | B4 Verdict surfaces | S3 | B1 | Implemented | Canonical run snapshot, board columns, PR/JIRA lines, Agent review progress, and evidence-based explain output |
| 6 | B6 Reviewer evaluation | S3 | B1 | Implemented | Hash-pinned seeded defects, clean control, per-class catch rates, and separate simulated/real-model evidence |
| 7 | B2 Bounded repair | S4 | B1, B6 | Implemented | Bounded per-repo repair, revalidation, rereview, metering, and unresolved-finding history |
| 8 | B3 Delivery policy | S4 | B1, B2, B4 | Next eligible | `off|warn|require`, pre-gate refusal, unavailable policy, constitution pins |
| 9 | B5 Cost containment | S3/S4 | B1–B3 | Planned | Judgement-tier pin, budget-envelope uplift, reviewer-panel deferral trigger |
| 10 | A3 Plan-first from PR | S5 | A1, A2, B1–B4 | Planned | Extend the existing plan-state lifecycle to PR keys and PR intake |

The order follows the PRD delivery slices. A3 remains last because the PRD makes
fusion and reviewer delivery prerequisites for a useful, trustworthy PR plan.

## A1 acceptance mapping

| Criterion | Implementation | Verification |
| --- | --- | --- |
| A1.1 earned grammar | `ticket_discovery.py` imports and calls `catalog/bootstrap/correlate.py::jira_keys`; `correlate.py` is import-safe without changing script execution | False-positive and valid-key extraction tests |
| A1.2 validation | Pipeline validates every candidate through `TRACKER get_item`; adapters distinguish not-found (`3`) from unavailable; invalid keys become `discovered_invalid` | Unit state tests, mock adapter test, adapter contract review |
| A1.3 deterministic ambiguity | Explicit valid key wins; otherwise one validated branch key wins; unresolved multi-key cases produce `ambiguous`, omit ticket use, comment candidates, and request explicit requeue | Priority, ambiguity, queue-dedupe, and pipeline structure tests |
| A1.4 provenance | Discovery artifact records signal, validation, rejection, outcome, and reason; run record snapshots it; `make explain` renders the decision evidence | Run-record/explain integration test |
| A1.5 no discovery | With the flag enabled and no candidate, the prompt tail says exactly `No ticket discovered.`; with the flag disabled no new files, ports, or phase arguments are used | No-key context assertion and default-off pipeline checks |
| A1.6 terminal status | Selected-ticket provenance records bounded status/category evidence; Closed/Done or JIRA `done` category warns in discovery/fused context, explain, and live/historical PR comments without refusing | classifier, forged-evidence, cross-surface, functional pipeline tests |
| A1.7 TaskEvent key | TaskEvent schema and receiver accept an optional PR key, reuse the work-queue ticket grammar, and preserve the exact pre-key PR digest | Schema pin, exact-digest, keyed/unkeyed replay, invalid-type/refusal, corrected-retry, and queue-shape tests |

### A1.7 — TaskEvent explicit-key parity

Implemented in the normalized TaskEvent schema and receiver: `mode: pr` may
carry the same optional bare key as queue/API/wizard intake. The receiver uses
the existing work-queue validator, rejects invalid values before recording the
event as seen, and keeps the historical empty key slot in PR-event dedupe. The
schema and receiver behavior are pinned together in focused tests. Detailed
evidence is in
[pr-jira-fused-context-a1-7-implementation-plan.md](pr-jira-fused-context-a1-7-implementation-plan.md).

## Remaining item plans

### A2 — Context fusion

The implemented, acceptance-mapped design and validation evidence are maintained in
[pr-jira-fused-context-a2-implementation-plan.md](pr-jira-fused-context-a2-implementation-plan.md).
It first closes response-identity validation, then promotes the already-fetched
selected ticket to the canonical path, shares guidance selection, renders a
budget-aware untrusted-data block at the run-specific prompt tail, and pins A1
flag-off/no-selection parity.

### A4 — Discovery evaluation

Implemented with a versioned QE-owned label set covering every production signal
plus absent, invalid, and conflicting outcomes. `make eval` runs the production
discovery policy over synthetic SCM/Tracker evidence, reports validation-aware
precision/recall per signal, treats ambiguity refusal as a correct final
decision, and enforces the fixed 95% M1 floor. Every figure is labelled
`simulated`; the discovery flag remains default off pending real-estate labels.
Detailed evidence is in
[pr-jira-fused-context-a4-implementation-plan.md](pr-jira-fused-context-a4-implementation-plan.md).

### B1, B4, B6 — Advisory reviewer slice

Define a schema-pinned read-only reviewer phase after validate and before gate.
Its contract compares generated tests with the plan/triage contract, fused
acceptance criteria, and repository conventions. Persist `approve`,
`needs_work`, `unavailable`, or deterministic `skipped`, then surface it without
changing human review status. Attack the contract with each seeded defect class
and a clean control.

B4 is implemented as a total run-scoped `review` projection over B1 evidence.
It records the effective policy and initializes B2's loop/unresolved fields,
then drives the PR and JIRA summary lines, CLI and dashboard review-board
columns, wizard and run-progress steps, and `make explain`. Human review state
remains a separate store and transition path. Detailed acceptance and evidence
are in [pr-jira-fused-context-b4-implementation-plan.md](pr-jira-fused-context-b4-implementation-plan.md).

B6 is implemented as an attack harness over a versioned, SHA-pinned QE-owned
fixture set. The default evaluator sends scripted fixture contracts through the
production reviewer boundary and labels its 100% M3 result `SIMULATED`—proof of
plumbing, not judgement. It also checks one clean approve control. A separate,
explicit and potentially billable `make reviewer-eval-real` command attacks the
same fixtures through the configured provider; until parity authentication is
restored, every renderer says that real-model quality is blocked and unmeasured.
Detailed acceptance and evidence are in
[pr-jira-fused-context-b6-implementation-plan.md](pr-jira-fused-context-b6-implementation-plan.md).

### B2 — Bounded repair

B2 is implemented behind the existing reviewer flag. A needs-work verdict
selects only repositories with unresolved findings, runs the named
`reviewrepair` authoring phase against existing generated files, revalidates the
merged generation contract, and fans the read-only reviewer out again. The
loop is independently capped by `review.max_loops` (default one); repair,
validation, and rereview use unique metered labels and the budget guard remains
the hard stop. Strict nested evidence records every iteration while carrying
unaddressed or repeated findings forward even if a later raw verdict says
approve. Write-enabled repair products are structurally excluded from both
phase caches. Detailed acceptance and evidence are in
[pr-jira-fused-context-b2-implementation-plan.md](pr-jira-fused-context-b2-implementation-plan.md).

### B3, B5 — Require and cost slice

Implement `off|warn|require` before the gate, amend and pin the constitution,
keep critic and reviewer distinct, and document consequence/cost language.

### A3 — Plan-first from PR

Teach plan intake to carry a PR target while reusing `plan_state` and existing
approval revocation/resume rules. Author from diff plus A2 fused context, comment
the ticket when present and the PR always, omit plan-only run records, and add
the same option to the wizard and queue.

## Iteration gate

Each item requires focused tests, the broadest practical compatibility suite,
two-pass review reports, exact-file staging, cached whitespace checks, a commit
whose message names the item, push, and verified upstream parity before the next
item begins.
