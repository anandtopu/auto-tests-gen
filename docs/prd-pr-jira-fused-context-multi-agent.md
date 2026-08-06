# PRD — Fused PR + JIRA context, and agent-reviewed generation

|  |  |
|---|---|
| **Status** | Draft for review |
| **Author** | Product Management (QE Platform) |
| **Date** | 2026-08-06 |
| **Doc** | `docs/prd-pr-jira-fused-context-multi-agent.md` |
| **Related** | [prd-test-knowledge-base.md](prd-test-knowledge-base.md) (this PRD resolves its open question **D7**) · [architecture.md](architecture.md) §5.3, §5.8 · `specs/platform/constitution.yaml` |

Two initiatives, one document, because they meet in the same place: the context a
generation run works from, and the agents that work it.

1. **Fused context** — a pull request usually implements a JIRA ticket. Today the
   platform reads one or the other, never both. When a PR triggers test
   generation, the ticket's acceptance criteria are the *requirements* the diff
   is implementing — and the run cannot see them.
2. **Agent-reviewed delivery** — every generated test should face an agent
   reviewer before a human sees it. The platform already runs several agents per
   workflow; what it lacks is a reviewer of the *generated tests themselves*
   whose findings drive repair, and a policy for what its verdict means.

---

## 1. Problem statement

**A. The two workflows are blind to each other's context.** Workflow A (PR) sees
the diff, the catalog slice, conventions and coverage gaps — but not the ticket
the PR implements, even when the branch is literally named `feature/PROJ-301-…`.
Workflow B (JIRA) sees the full ticket but no code change. A PR-triggered run
therefore tests *what changed* without knowing *what was asked*: acceptance
criteria, edge cases named by the reporter, and the issue type (a bug demands a
regression test; a story demands coverage of new behaviour) are all absent. The
generated tests assert what the diff does, which is circular when the diff is
wrong — the exact case a ticket's acceptance criteria would catch.

**B. Review before delivery is deterministic-only or human-only.** Between
generation and a human reviewer sit today: schema validation, the deterministic
gate (lint, execution, scope, secrets), and an advisory critic whose score
nothing may act on (constitution C2). Nothing performs an *agent review with
consequences*: reading the tests against the plan and the ticket, finding the
scenario that was never covered or the assertion that asserts nothing, sending
findings back for a bounded repair — and, where a team opts in, refusing to
deliver until the finding is addressed.

---

## 2. Users

| Persona | What changes for them |
|---|---|
| **DEV** (PR author) | PR-triggered tests reflect the ticket's acceptance criteria, not just the diff; the PR comment says which ticket informed generation |
| **QA** (reviewer) | Arrives at a plan or test set that an agent has already challenged, with the findings and their resolutions visible — review starts at "what did the reviewer miss", not "what is obviously wrong" |
| **LEAD** | Can require agent review to pass before anything reaches the board; can author a reviewable test *plan* from a PR, not only from a ticket |

---

## 3. Goals and non-goals

### 3.1 Goals

1. A PR-triggered run discovers and fuses its JIRA ticket into generation
   context, with provenance for *how* the ticket was found.
2. Plan-first mode works from a PR: author a plan from diff + ticket, stop for
   human approval, resume to generation — resolving prior-PRD **D7**.
3. Generated tests face an adversarial agent reviewer whose findings drive a
   bounded repair loop before anything reaches a human.
4. Teams choose what the reviewer's verdict means (`off | warn | require`),
   rolled out in that order.
5. Every new agent call is metered, skippable, and honest about cost.

### 3.2 Non-goals

- **Not** replacing the deterministic gate. It remains the only commit/push path
  and stays LLM-free. The reviewer can stop a run *before* the gate, the way any
  failing phase already can; it never adds an LLM opinion *inside* the gate.
- **Not** touching the critic. C2 stands: the critic stays advisory, read-only,
  and unread by the gate. The reviewer is a *different phase* with different
  rules, stated in §7 and pinned.
- **Not** auto-approving anything on the human review board. An agent verdict
  can block delivery; it can never *substitute* for a human approval
  (mirroring: reuse can never auto-approve a plan).
- **Not** two-way JIRA sync, ticket editing, or transition automation. The
  ticket is read as context and commented on; its workflow state is not ours.
- **Not** a general multi-agent framework. The platform's agent topology is
  phases + fan-out + adversary pairs, deliberately bounded; this PRD adds one
  reviewer pair to that topology, not an orchestration layer.

---

## 4. What exists today (verified against the code, not the docs)

Same discipline as the prior PRD: each claim was checked before it was written,
because the expensive failure is proposing what exists.

**Multi-agent machinery — more exists than the ask assumes:**

| Capability | Where | Verified |
|---|---|---|
| Per-repo generation fan-out (one agent per test repo, confined writes, merged contracts, contained failures) | `pipeline.sh` GENERATE + `merge_contracts.py` | ≥2 repos → own agent, own conventions, `{{TARGET_REPO}}` confinement |
| Adversarial **plan** review (read-only adversary + arbiter that may only ADD) | `plan_adversary.py`, `prompts/jira-plan-{adversary,arbitrate}.md` | adversary `allowed_tools: "Read"`; non-fatal; runs before human approval |
| Advisory critic (0.0–1.0 quality score) | `prompts/critic.md`, constitution **C2** | read-only, gate never reads it, cannot move review status — all three pinned |
| Execute-and-repair loop | `validate` phase, `validate-repair.md`, org-config `repair_loops: 3` | tests are run and repaired up to 3 times |
| Per-phase tool allow-lists and turn caps | org-config `phases:` | every agent already runs least-privilege |
| Budget guard + degradation ladder + skip rules | `budget.py`, `SKIP_PHASE` | judgement phases never downgrade; no-op phases skip deterministically |

**Context available to the PR path today:** the real diff, the routed catalog
slice, repo conventions/exemplars, coverage gaps, estate knowledge — and **no
ticket**. `TRACKER get_item` is called only in the `jira|plan|tests` modes; the
`pr` branch never touches the Tracker port.

**Precedents this PRD builds on rather than reinvents:**

- A hardened JIRA-key extractor already exists:
  `catalog/bootstrap/correlate.py::jira_keys()` — 2–10 uppercase project key,
  with the false positives (`UTF-8`, `HTTP-2`, `SHA-1`, `RFC-2616`) already
  fought and excluded. Discovery reuses it; a second regex would re-fight that
  war.
- Plan-first exists end-to-end for JIRA keys: draft → human approval →
  `require_approved` resume → gate. PR keys (`PR-<repo>-<n>`) already flow
  through run records, review state, and the queue.
- The `spec.enforce: off|warn|strict` two-step rollout pattern, and the
  refusal-with-named-fix idiom, are the templates for the reviewer's delivery
  policy.
- Issue-type guidance (`prompts/issue-types/{story,bug,security}.md`) exists and
  is selected by ticket type — today only on the JIRA path.

**The gaps, numbered:**

| # | Gap | Evidence |
|---|---|---|
| **G1** | PR runs never see the ticket the PR implements | `get_item` absent from the `pr` branch of `pipeline.sh` |
| **G2** | Plan-first cannot start from a PR | `plan` mode validates a ticket key; prior PRD filed this as D7 |
| **G3** | No ticket linkage field at PR intake | `work_queue.add(mode, target, pr, release, …)` has no ticket parameter; the wizard and PR-URL intake cannot carry one |
| **G4** | Nothing reviews the generated tests as an agent with consequences | between `validate` and the gate there is only the critic, which C2 forbids from acting |
| **G5** | Issue-type guidance never reaches PR runs | selected from `out/ticket.json`, which PR runs don't have |

---

## 5. Epic A — Fused PR + JIRA context

### A1. Ticket discovery from a pull request

**Requirement.** WHEN a PR-triggered run starts, THE SYSTEM SHALL attempt to
identify the JIRA ticket(s) the PR implements, from these signals in priority
order — an explicit statement always beating an inference:

1. An explicit ticket key supplied at intake (new queue/API/wizard field — G3).
2. The PR's source branch name (`feature/PROJ-301-discounts`).
3. The PR title and description.
4. The commit messages in the PR's range.

**Acceptance criteria:**

- **A1.1** — Extraction SHALL reuse `correlate.py::jira_keys()` (one definition;
  its false-positive exclusions are earned knowledge).
- **A1.2** — A discovered key SHALL be validated by `TRACKER get_item` before
  use. A key that does not resolve is recorded as `discovered_invalid` and NOT
  used — a plausible-looking key pointing at the wrong or dead ticket poisons
  context worse than no ticket at all.
- **A1.3** — WHEN multiple distinct keys survive validation, THE SYSTEM SHALL
  prefer the branch-name key; absent that, it SHALL proceed **without** a ticket
  and record `ambiguous` naming every candidate — guessing between two tickets
  is the one wrong answer that looks right. The PR comment lists the candidates
  so a human can requeue with an explicit key.
- **A1.4** — Discovery provenance (which signal produced the key, what was
  validated, what was rejected) SHALL land in the run record and be answerable
  by `make explain` — "why did generation cite PROJ-301?" must have a recorded
  answer, per the explainability rule that a decision whose reason was not
  written down comes back `unexplained`.
- **A1.5** — WHEN no ticket is discovered, the run SHALL proceed exactly as
  today AND the context assembly SHALL state "no ticket discovered" explicitly
  (C13: not-found is its own state, distinct from not-looked).

### A2. Context fusion

**Requirement.** WHERE a ticket was discovered and validated, THE SYSTEM SHALL
add it to the PR path's authoring context (triage and generate; plan phases per
A3), as **data, never instructions**.

**Acceptance criteria:**

- **A2.1** — The ticket block SHALL reuse the existing single-parse machinery
  (`out/ticket.json` + `ticket_fields.py`) — the PR path gains the same fields
  the JIRA path reads, through the same code.
- **A2.2** — Issue-type guidance SHALL now be selected on the PR path too (G5):
  a PR implementing a bug ticket gets the regression-test guidance a JIRA-
  triggered run would get.
- **A2.3** — Placement SHALL respect prompt-caching order: the ticket block
  joins the run-specific tail (beside the diff), never above the stable estate
  prefix — the `agent_context` lesson (most-stable-first) applies verbatim.
- **A2.4** — The fused block SHALL ride the existing context-scoping budget
  where scoping is on for the phase; the ticket's acceptance criteria are
  MUST-KEEP (they are the requirements — dropping them to fit a budget defeats
  the fusion), while description prose competes normally.
- **A2.5** — With the flag off, or no ticket found, the PR path SHALL be
  byte-identical to today (pinned).

### A3. Plan-first from a pull request (resolves prior-PRD D7)

**Requirement.** `plan` mode SHALL accept a PR target: author a test plan from
diff + fused ticket, stop for human review/approval, and resume through the
existing `tests` mode into generation and the gate.

**Acceptance criteria:**

- **A3.1** — The plan-first lifecycle for a PR key SHALL be the same state
  machine JIRA keys use (draft → approved → generated), through `plan_state` —
  no parallel implementation. Editing revokes approval; resume refuses a draft;
  the adversary/arbiter pair challenges the plan before the human sees it.
- **A3.2** — WHERE a ticket was discovered, the plan SHALL be commented on that
  ticket (the JIRA path's existing comment); the PR SHALL receive the plan link
  in both cases — the surface the requester watches is the one that must speak.
- **A3.3** — `plan` mode for a PR SHALL write no run record (matching JIRA plan
  mode: it never reaches the gate, and commit-rate metrics stay honest).
- **A3.4** — The wizard and queue SHALL offer "plan first" for PR intake exactly
  as they do for tickets today — same ladder, same approval step.

### A4. Discovery evaluation

**Requirement.** Discovery precision SHALL be measured before the flag defaults
on, on a labelled fixture set.

- **A4.1** — The demo estate already carries JIRA-keyed commit history
  (`demo-bootstrap` rebuilds it); fixtures SHALL cover: key on branch, key only
  in commits, no key anywhere, invalid key, and two conflicting keys.
- **A4.2** — Reported as precision/recall per signal in `make eval`, with the
  ambiguous-and-refused case counted as **correct refusal**, not as a miss —
  rewarding a guess here would train the wrong behaviour in.

---

## 6. Epic B — Agent-reviewed generation

### B1. The test reviewer

**Requirement.** After `validate` and before the gate, a **read-only reviewer
agent** SHALL examine the generated tests against: the plan's scenarios (or the
triage contract on the PR path), the fused ticket's acceptance criteria when
present, and the target repo's conventions. It SHALL emit findings — each with a
severity, the file/test it concerns, and what a fix looks like — and a verdict:
`approve` or `needs_work`.

**Acceptance criteria:**

- **B1.1** — The reviewer SHALL be read-only (`allowed_tools: "Read"`), for the
  reason org-config already records for the critic: a reviewer that can edit is
  an unreviewed repair loop, not a second opinion. The plan adversary's
  containment argument applies verbatim.
- **B1.2** — The reviewer looks for what *execution cannot reveal*: an
  acceptance criterion with no covering test, a vacuous or tautological
  assertion, a test asserting the diff's behaviour where the ticket asked for
  different behaviour, convention violations the lint does not encode. It SHALL
  NOT re-run tests (validate owns execution) or re-litigate the plan (the plan
  adversary owns that, pre-approval).
- **B1.3** — A reviewer failure (crash, timeout, malformed contract) SHALL be
  non-fatal and recorded as `reviewer: unavailable` — distinct from `approve`
  (C13: not-reviewed is never reported as reviewed-and-passed). Delivery policy
  `require` (B3) treats `unavailable` per org-config `review.on_unavailable:
  proceed|hold`, default `proceed` with the state visible everywhere the
  verdict shows.
- **B1.4** — Zero generated tests SHALL skip the reviewer via the existing
  deterministic skip machinery (`SKIP_PHASE`), rendered as skipped, not passed.

### B2. Bounded repair from findings

**Requirement.** WHEN the verdict is `needs_work`, the findings SHALL drive one
bounded repair pass: the generator applies fixes, `validate` re-executes, and
the reviewer re-examines.

**Acceptance criteria:**

- **B2.1** — Review loops SHALL be capped by org-config `review.max_loops`
  (default **1**) — separate from `validate`'s `repair_loops`, because each loop
  is 2–3 LLM calls and the budget guard's exit-77 remains the hard backstop.
- **B2.2** — Each loop SHALL be metered into the run's spend like any phase;
  findings, fixes applied, and the verdict per iteration land in the run record.
- **B2.3** — A finding the repair pass does not resolve SHALL survive into the
  final verdict — a repair loop that launders findings into silence is worse
  than no reviewer.

### B3. Delivery policy — what a verdict means

**Requirement.** org-config `review.agent_gate: off | warn | require` SHALL
decide the consequence of a final `needs_work` verdict, defaulting to **warn**.

**Acceptance criteria:**

- **B3.1** — `off`: reviewer does not run. `warn`: verdict and findings are
  recorded and surfaced everywhere (B4) but the run proceeds to the gate.
  `require`: a final `needs_work` **fails the run before the gate executes** —
  nothing is committed, the run record states the refusing findings, and the
  PR/ticket comment names the fix and the override (requeue with
  `review.agent_gate=warn` requires the same permission as editing org-config).
- **B3.2** — The rollout SHALL be two-step by default and documented in the
  Settings UI in consequence language, exactly as `spec.enforce` is: turning on
  `require` first just teaches people to bypass the reviewer.
- **B3.3** — The reviewer SHALL only ever run **pre-gate**. It never renders a
  verdict on already-committed tests — an after-the-fact rejection of a pushed
  test is the "reviewer believes it is gone while it runs in CI" lie the
  selective-approval work exists to prevent. Post-commit disposition stays
  human, through the existing selection workflow.
- **B3.4** — **Constitution amendment (new clause, this PRD's condition of
  shipping B3):** *the reviewer may stop a run before the gate; the gate never
  reads its output; it has no write tools; its verdict alone never moves a
  review-board status.* Each sub-claim pinned. C2 (critic) is untouched — and a
  pin SHALL assert the critic and reviewer remain distinct phases, so the
  reviewer's gating power can never be quietly transferred to the critic.

### B4. Verdict surfaces

**Requirement.** The verdict SHALL appear everywhere the run does: run record
block (`review: {verdict, findings, loops, unresolved}`), review-board column,
PR coverage comment and JIRA comment line, wizard/Run-progress step ("Agent
review" between "Validate" and "Quality gate"), and `make explain` (which
findings, what was repaired, what survived).

- **B4.1** — On the human review board, the agent verdict is *context, not a
  decision*: it can never set `approved`/`changes_requested` (B3.4 pin).

### B5. Cost containment

- **B5.1** — The reviewer is a **judgement phase**: it joins the
  never-downgrade list beside testplan/adversary/generate — a reviewer on the
  cheap tier rubber-stamps.
- **B5.2** — Envelope accounting: `review.agent_gate: warn|require` adds ~1–3
  calls per run; the per-workflow envelopes gain a documented uplift, and the
  intake envelope warning reflects it.
- **B5.3** — A reviewer **panel** (multiple lenses) is explicitly DEFERRED with
  a written trigger: adopt only if the single reviewer's measured escape rate
  (defects a human finds that the reviewer saw and missed) exceeds an agreed
  threshold over a real quarter. One reviewer that gates is worth shipping;
  three that vote is a cost multiplier in search of evidence.

### B6. Reviewer evaluation

**Requirement.** The reviewer SHALL be evaluated the way the gate is — by
attack, not by inspection.

- **B6.1** — Seeded-defect fixtures: a vacuous assertion, an uncovered
  acceptance criterion, a test contradicting the ticket, a convention breach.
  The reviewer must catch each; the eval reports catch rate per defect class.
- **B6.2** — A clean fixture must yield `approve` with zero findings — a
  reviewer that always finds something trains humans to ignore it.
- **B6.3** — Real-model review quality (vs. the mock's scripted verdicts)
  is blocked on the same parity auth as every other quality claim, and SHALL be
  stated that way wherever eval results render (simulated figures labelled).

---

## 7. Success metrics

| # | Metric | Baseline | Target | Method |
|---|---|---|---|---|
| M1 | Ticket-discovery precision on fixtures | n/a (new) | ≥95%, with ambiguous-refusal counted correct | A4 eval in `make eval` |
| M2 | PR runs with fused ticket context | 0% | ≥70% of PR runs in estates using key-bearing branch/commit conventions | run-record provenance |
| M3 | Reviewer catch rate on seeded defects | n/a (new) | 100% of B6.1 classes in mock; real-model rate reported when parity unblocks | B6 eval |
| M4 | Findings resolved by the bounded repair | n/a | ≥60% of findings resolved within `max_loops: 1` | run records |
| M5 | Plan-from-PR adoption | 0 (impossible today) | ≥30% of PR-path work in plan-first estates goes through a plan within a quarter | plan_state + run records |
| M6 | Escaped noise (critic §5.8.7 proxy: defects reaching human review) | scorecard baseline at S3 ship | −40% where `agent_gate ≥ warn` | scorecard, conditioned on the knob |
| **Guardrails** | Commit rate (mechanics), p95 wall-clock, cost per run vs envelope | current | no regression; cost uplift within the documented envelope delta | scorecard + cost report |

Same honesty rule as the prior PRD: mock-derived figures guard mechanics, not
quality; every simulated number renders labelled; M3/M6 real-model claims are
gated on parity auth being restored.

---

## 8. Delivery plan

Every slice behind a named flag or org-config knob, default preserving today's
behaviour byte-for-byte (pinned per slice).

| Slice | Scope | Flag / knob | Exit criteria |
|---|---|---|---|
| **S1 — Discovery + fusion** | A1, A2, G3 intake field | `AIQE_PR_TICKET_CONTEXT` (default 0) | discovery with provenance; validation + ambiguity refusal; issue-type guidance on PR path; flag-off byte-identical |
| **S2 — Discovery eval** | A4 | — (eval always on) | fixture set incl. conflict + invalid cases; precision/recall in `make eval`; M1 met in mock |
| **S3 — Reviewer, advisory** | B1, B4, B6 | `AIQE_TEST_REVIEWER` (default 0) + `review.agent_gate: warn` | read-only reviewer; verdict on every surface incl. explain; skip-on-zero-tests; seeded-defect eval green |
| **S4 — Repair + require** | B2, B3, constitution clause | `review.agent_gate: require` (opt-in) | bounded repair metered; pre-gate refusal with named fix; new clause + pins landed; critic/reviewer distinctness pinned |
| **S5 — Plan-from-PR** | A3 | `AIQE_PR_PLAN` (default 0) | full plan-first lifecycle on a PR key through existing plan_state; wizard/queue entry points; prior-PRD D7 closed with a pointer here |

S5 deliberately last: fusion (S1) makes a PR-authored plan worth reading, and
the reviewer (S3/S4) is what makes the resumed generation trustworthy.

---

## 9. Risks

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| R1 | **Wrong ticket fused** — a stale key in a branch name poisons context with another feature's requirements | Medium | High | A1.2 existence validation; A1.3 refusal on ambiguity; provenance in explain; explicit intake key beats all inference; PR comment names what was used |
| R2 | **Reviewer false-rejects** block delivery under `require` | Medium | High | default `warn`; `require` opt-in after a measured warn-mode period (B3.2); B6.2 clean-fixture check; override path documented |
| R3 | **Cost growth** — up to 3 extra calls/run | High | Medium | B5 skip rules, judgement-tier-only, envelope uplift documented, exit-77 backstop; panel deferred with trigger (B5.3) |
| R4 | **Constitution erosion** — an LLM verdict acquiring gate-adjacent power drifts toward LLM-in-the-gate | Low | High | B3.4 clause with per-claim pins; reviewer stops runs the way failing phases already do; gate code never reads reviewer output (pinned) |
| R5 | **Ticket text as attack surface** widens to the PR path — a hostile ticket now reaches PR-triggered prompts | Medium | Medium | same data-never-instructions framing the JIRA path already enforces; the injection-fixture pattern from [prd-test-knowledge-base.md](prd-test-knowledge-base.md) (its A1.6) extends to a fused-ticket fixture |
| R6 | **Latency** — plan-from-PR adds a human stop to a path that was fire-and-forget | Low | Low | plan-first is opt-in per request (queue "Plan only"), never the PR default |

---

## 10. Open questions

| # | Question | Owner | Needed by |
|---|---|---|---|
| E1 | Which discovery signals default on for real estates? Branch-naming conventions vary; commit-message mining may need per-estate enablement | QE Lead | S1 |
| E2 | Under `require`, should a refused run auto-create a review-board item assigned via the reviewer rota, or only comment? | LEAD + Product | S4 |
| E3 | Reviewer model tier: generate-tier quality vs. validate-tier cost — decide from S3 warn-mode data, not upfront | EM | S4 |
| E4 | Panel trigger threshold for B5.3 (escape-rate % that justifies a second lens) | Product | after one real quarter |
| E5 | Should discovered tickets auto-link the PR on the ticket (Tracker `comment`) even when generation is not plan-first? | Product | S1 |

---

## 11. Constraints (inherited, non-negotiable)

The seven standing rules from the prior PRD apply unchanged — gate as sole
committer, ticket/PR text as data, C12 no silent fallback, C13 distinct
not-established states, generated-never-outranks-owned, catalog evidence-based,
simulated figures labelled — plus its three engineering rules (state placement
follows the deployed shape; `fs_lock` + the unlocked-RMW pin; isolation knobs
from day one). New state introduced here (verdict blocks, discovery provenance)
lives inside the existing run record and plan state, deliberately: no new store,
so no new store risks.

---

## Appendix A — Worked example

PR #218 on `orders-api`, branch `feature/PROJ-310-partial-refunds`, three
commits, two mentioning PROJ-310.

1. **Discovery**: intake had no explicit key; branch yields PROJ-310;
   `get_item` validates it. Provenance: `branch-name, validated`.
2. **Fusion**: triage and generate receive the diff *and* PROJ-310's acceptance
   criteria ("refund may not exceed captured amount"; "partial refund emits
   `refund.partial` event"). Issue type `story` selects story guidance.
3. **Generation**: the diff never touches the event emitter — but the AC does.
   The generated set includes an event-emission test the diff alone would never
   have suggested.
4. **Agent review** (`warn`): finding — "AC 'refund may not exceed captured
   amount' has no negative-path test; suggest boundary case at captured+0.01".
   One repair loop adds it; validate re-executes; verdict `approve`.
5. **Delivery**: gate runs as ever; PR comment cites PROJ-310, the verdict, and
   the repaired finding; the ticket gets the coverage line; the review board
   shows the verdict as context beside the human's pending decision.

The human reviewer's first question — "did it test what the ticket asked, not
just what the code does?" — was asked by an agent, answered, and recorded,
before a human spent a minute.
