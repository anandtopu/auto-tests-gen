# Fused PR + JIRA Context and Agent Review — Implementation Plan

Date: 2026-08-06
Source: [prd-pr-jira-fused-context-multi-agent.md](prd-pr-jira-fused-context-multi-agent.md)

## Delivery order and status

| Order | Item | Slice | Dependencies | Status | Implementation boundary |
| ---: | --- | --- | --- | --- | --- |
| 1 | A1 Ticket discovery | S1 | none | Implemented | Opt-in SCM metadata collection, earned-key extraction, Tracker validation, deterministic selection/refusal, intake field, provenance, explain |
| 2 | A2 Context fusion | S1 | A1 | Implemented | Exact response identity, canonical `out/ticket.json`, shared guidance selection, scoped prompt tail, mandatory acceptance-criteria retention, flag-off parity; evidence in [pr-jira-fused-context-a2-implementation-plan.md](pr-jira-fused-context-a2-implementation-plan.md) |
| 3 | A4 Discovery evaluation | S2 | A1 | Next eligible | Labelled signal/conflict fixtures plus per-signal precision, recall, and correct-refusal metrics |
| 4 | B1 Test reviewer | S3 | none; schedule after S2 | Planned | Read-only pre-gate reviewer contract, deterministic skip, unavailable state |
| 5 | B4 Verdict surfaces | S3 | B1 | Planned | Run record, board, comments, progress, and explain surfaces |
| 6 | B6 Reviewer evaluation | S3 | B1 | Planned | Seeded defects, clean control, simulated/real-model labelling |
| 7 | B2 Bounded repair | S4 | B1, B6 | Planned | One separately metered findings-driven repair/revalidate/rereview loop |
| 8 | B3 Delivery policy | S4 | B1, B2, B4 | Planned | `off|warn|require`, pre-gate refusal, unavailable policy, constitution pins |
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
| G3 explicit intake | Queue/API/wizard accept one optional bare ticket key; it is PR-only and part of dedupe identity | Queue validation, propagation, dedupe, and UI/server structure tests |

## Remaining item plans

### A2 — Context fusion

The implemented, acceptance-mapped design and validation evidence are maintained in
[pr-jira-fused-context-a2-implementation-plan.md](pr-jira-fused-context-a2-implementation-plan.md).
It first closes response-identity validation, then promotes the already-fetched
selected ticket to the canonical path, shares guidance selection, renders a
budget-aware untrusted-data block at the run-specific prompt tail, and pins A1
flag-off/no-selection parity.

### A4 — Discovery evaluation

Add labelled branch-only, commit-only, absent, invalid, and conflicting fixtures.
Extend `make eval` with per-signal precision/recall and an explicit
correct-refusal category. Keep mock metrics labelled simulated.

### B1, B4, B6 — Advisory reviewer slice

Define a schema-pinned read-only reviewer phase after validate and before gate.
Its contract compares generated tests with the plan/triage contract, fused
acceptance criteria, and repository conventions. Persist `approve`,
`needs_work`, `unavailable`, or deterministic `skipped`, then surface it without
changing human review status. Attack the contract with each seeded defect class
and a clean control.

### B2, B3, B5 — Repair and require slice

Add a separately budgeted loop whose findings feed a confined repair phase,
then validate and review again. Preserve unresolved findings across iterations.
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
