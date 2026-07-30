# Roadmap architecture — design and implementation plan for the top-10

Companion to `docs/product-roadmap.md`. One section per prioritized item: design,
data flow, files touched, test strategy, and an honest build-now / next-iteration
call. The platform's invariants bind every design:

- the gate is the only writer to any repo; the critic and every new signal stay
  advisory;
- routing and scoring stay deterministic wherever an LLM adds nothing;
- external text (tickets, PRs, webhook bodies) is data, never instructions;
- every state mutation goes through `fs_lock` (locked, atomic, quarantining);
- every new phase or endpoint must work under `AIQE_MOCK=1` without spend.

## Build sequencing

**This iteration (built below):** 1.1 CI auto-ingest · 1.5 reviewer nudges ·
3.2 risk-weighted gaps · 4.5 adversary verdicts in UI · 2.1 extend scout
(deterministic) · 5.4 scheduled maintenance.

**Next iteration (designed here, not built):** 4.1 in-UI diff review · 1.2 flake
quarantine · 3.1 traceability matrix · 6.1 similar-plan retrieval.

Rationale: the built set is dependency-light, deterministic, and each lands with
tests in one pass. The deferred four are UI-heavy (4.1), depend on 1.1 accumulating
real data (1.2), or introduce retrieval infrastructure that deserves its own
review cycle (6.1).

---

## 1.1 CI results auto-ingest — BUILD NOW

**Problem.** `test health: n/a` forever, because ingestion is a manual make target.

**Design.** New receiver route `POST /hooks/ci/results`, token-gated like every
hook. Body is raw JUnit XML (or Jenkins JSON) — *not* JSON-wrapped, so a CI job can
`curl --data-binary @results.xml`. The receiver writes the body to a temp file and
calls the existing `test_health.ingest(path)`; the response reports matched /
unmatched counts so the CI job's log shows whether mapping worked.

Constraint discovered in review: the receiver's `do_POST` parses the body as JSON
*before* routing — XML would 400 at the front door. The route check moves before
the parse; only JSON routes parse JSON.

**Data flow.** CI job → POST XML → temp file → `test_health.ingest` →
`catalog/health.json` (atomic, locked) → scorecard "Test health" + critic context
+ future flake quarantine.

**Files.** `bin/taskevent_receiver.py`, tests in `test_hooks_auth.py` pattern
(new: `test_ci_ingest.py`). **Risk:** payload size — cap at 5 MB, reject larger.

## 1.5 Reviewer assignment + review debt — BUILD NOW

**Problem.** `acceptance rate: n/a` — the board works but nobody is ever asked.

**Design.** Optional `review.reviewers: [a, b, ...]` in org-config. When a
committing run auto-resets a key to `pending_review` (`review_state.auto`), assign
`assigned_to` round-robin — persisted counter in the reviews store itself
(`_assign_cursor` key, skipped by every consumer that iterates keys — precedent:
run-record globs skip state files). The board, the digest email and the runs view
show assignee and age. No enforcement: assignment is a nudge, approval still
records whoever actually acted.

**Files.** `engine/lib/review_state.py`, `registry/org-config.yaml`, `bin/qa.py`
(board columns), `engine/lib/email_notify.py` or `team_report.py` (digest line).
**Risk:** the cursor key polluting key iteration — every existing iterator must
skip it; pinned by test.

## 3.2 Risk-weighted gap ranking — BUILD NOW

**Problem.** `make gaps` lists uncovered surface flat; a payments POST ranks equal
to a static GET.

**Design.** Deterministic scoring in `coverage_gaps.py` (no LLM — this is exactly
the kind of judgement rules do better and reproducibly):

```
score = 1
+2  mutating method (POST/PUT/PATCH/DELETE) on an endpoint
+2  authz/payment-adjacent path token (auth, login, token, payment, admin, user)
+1  path takes an id/parameter ({...}) — state-addressing surface
```

`compute()` gains `uncovered_ranked: [{surface, score, reasons[]}]`; markdown
orders by score with reason tags. The pipeline context (`out/coverage-gaps.md`)
inherits the ordering, so generation and the plan adversary see the ranked list.

**Files.** `engine/lib/coverage_gaps.py`, tests. **Risk:** none — additive field,
existing consumers read `uncovered` untouched.

## 4.5 Adversary verdicts in the plan reviewer — BUILD NOW

**Problem.** The reviewer sees one summary line; the actual challenged gaps —
title, category, severity, rationale — die with the run's `out/` scratch.

**Design.** `plan_state.record_plan` already receives the summary; it now also
snapshots `plan_adversary.signal()` (the normalized gap list + accepted/rejected
counts) into the entry as `adversary_detail` — read from `out/` at record time,
because `out/` is per-run scratch and this is the last moment it exists.
`/api/plans/one` already spreads the entry; the plan editor renders a per-gap list
under the summary chip.

**Files.** `engine/lib/plan_state.py`, `bin/dashboard.py`. **Risk:** entry growth —
detail capped to the gaps list (≤ a few KB), no events.

## 2.1 Extend-vs-create scout — BUILD NOW (deterministic form)

**Problem.** `update-vs-create: 0%`. Generation now *sees* the catalog but the
extend decision is implicit. An LLM scout phase was the roadmap sketch; review
found a cheaper shape: the matching is mechanical — diff surface ∩ catalog
evidence — so a deterministic module does it with zero spend and zero mock
complexity, reserving LLM judgement for where it pays.

**Design.** `engine/lib/extend_scout.py`: parse path-like tokens and touched files
from `out/pr.diff`, intersect with each catalog test's evidence
(endpoints/routes), rank candidates, emit `out/extend-candidates.md`:

```
EXTEND suites/orders/discount.spec.js (e2e-api-tests-1)
  matched: POST /v1/orders/{id}/discounts   title: PROJ-88: applies % discount
```

Pipeline (pr branch) runs it after triage, tolerant (`|| : > file`); the file joins
the pr-path GENERATE context. The prompt already says "update existing tests
first"; the file gives it named targets. JIRA paths defer to next iteration
(scenario↔evidence matching is a different join).

**Files.** new `extend_scout.py`, `engine/pipeline.sh` (pr branch), `prompts/pr-generate.md`
(one line naming the context), tests. **Risk:** the pinned
generate-call-site test asserts specific files per line — additive file is fine
(assertions are `in`, not equality).

## 5.4 Scheduled maintenance — BUILD NOW

**Design.** `make maintain`: guidance sync (best-effort) → run-record prune →
OpenHands trace prune → state-bundle snapshot → one summary block. Scheduling
itself stays outside (cron/K8s CronJob calling the target) — the platform gains
the idempotent entry point, not a scheduler.

**Files.** `Makefile`. **Risk:** none; every step already exists and is tolerant.

---

## Designed now, built next iteration

### 4.1 In-UI diff review
Render `reports/runs/<id>-<repo>.diff` with a small hunk parser (no dependency),
side-by-side, per-hunk comment box appending to the review-board note, and the
existing Approve/Request-changes actions on the same screen. Server: one new
`GET /api/runs/diff?run=&repo=` (the raw file already serves; this adds parsed
JSON). Est. M — mostly front-end.

### 1.2 Flake quarantine
Blocked on 1.1 accumulating real pass/fail history. Then: a Flaky view over
`health.json` (`flaky: true` rows), `quarantine` verb writing a tag into the
catalog entry (via `qa.py map`, the sanctioned mutation path), and the gate's
execute step excluding quarantined specs from *blocking* while still running them.
The exclusion list must live in the test repo's `.ai-qe/config.yaml` so the repo
owns its own quarantine — the platform proposes, the repo's config disposes.

### 3.1 Traceability matrix
A read-only join, no new state: for each key — plan scenarios (plan contract) →
generated tests (run record, `repo`-stamped) → gate commits → health rows. One
`trace_matrix.py` emitting JSON/CSV + a dashboard table with CSV download. The
only design decision is identity: scenario→test linkage uses the `scenario_id`
already stamped on generated tests by the contract.

### 6.1 Similar-plan retrieval
Deliberately last: needs an embedding or lexical-similarity index over
`testplans/` + plan contracts. Start lexical (TF-IDF over scenario titles +
summary — stdlib-implementable, deterministic, offline); present top-1 prior plan
as a *diffed suggestion* in the plan view, never auto-applied. The safety line
from the roadmap holds: reuse is always mediated by a human seeing the diff.

## Verification protocol (every iteration)

1. Unit tests for the new module(s) + regression pins.
2. Full suite (`pytest registry/tests`).
3. Demo journeys J1–J6 under `AIQE_MOCK=1`.
4. `make serve` deploy check: endpoints 200, affected views render, zero console
   and server errors.
5. Bugs found → fix → re-run the affected layer, then the suite again.
6. Re-park the demo estate (PROJ-301 at draft).
