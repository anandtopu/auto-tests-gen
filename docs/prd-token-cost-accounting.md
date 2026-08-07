# PRD — Token-cost accounting: every request, durably, and a report you can audit

|  |  |
|---|---|
| **Status** | Draft for review |
| **Author** | Product Management (QE Platform) |
| **Date** | 2026-08-06 |
| **Doc** | `docs/prd-token-cost-accounting.md` |
| **Related** | [cost-optimization.md](cost-optimization.md) · [cost-reduction-architecture.md](cost-reduction-architecture.md) · [multi-llm-providers.md](multi-llm-providers.md) (provider-aware bases) · prior PRDs' single-count and instrumented-metric rules |

**The ask:** as an end user, track the token cost of each request processed —
test plan generation, E2E test generation, and every similar output — with an
authentic report giving accurate per-task LLM usage.

**The honest framing:** most of this exists, and this PRD says so before asking
for anything. What does *not* exist is worse than a missing feature — it is a
silent hole: **the requests most users care most about (plan-first) currently
leave no durable spend record at all.** This PRD closes that hole, unifies the
consumers the report cannot see, and adds the one thing "authentic" can mean
that the platform cannot yet do: reconcile its own telemetry against the
provider's bill.

---

## 1. What exists today (verified against the code — line numbers, not memory)

| Capability | Where | Status |
|---|---|---|
| Per-phase harvest: `input/output/cache_read/cache_creation` tokens, turns, `total_cost_usd` | `budget.phase_usage()` — telemetry is harvesting, not instrumentation | ✅ |
| Four cost bases that never cross: `reported` ($x) / `estimated` (~$x) / `local` ($0, tokens tracked) / `simulated` (~), `unknown` when unpriced | `budget.priced()`, run-record `spend` blocks | ✅ |
| Per-run/per-phase spend in run records; by-workflow/key/phase/tier report; `by_provider` rollup; local/cloud token split | `make cost-report`, `GET /api/cost-report`, Cost view, `qa.py status --cost`, `artifacts <KEY>` | ✅ |
| Enforcement: envelopes, degradation ladder, exit-77 ceiling, `enforceability()` naming what an unpriced provider disables | `budget.py` | ✅ |
| Baseline + regression alarm | `make cost-baseline`, `check-regression` in `make maintain` | ✅ |
| The iron rule: simulated figures always labelled; savings from simulation print n/a | everywhere costs render | ✅ |

**The gaps, with evidence:**

| # | Gap | Evidence |
|---|---|---|
| **G1** | **Plan-first and requirements requests leave no durable spend record.** `plan` mode runs 4 real LLM phases (analyze, testplan, adversary, arbiter — judgement-tier, the expensive ones) then exits at `pipeline.sh:922`, *before* the run-record writes at `:961`/`:1105`. The spend exists only in `out/cost.tsv` — which `pipeline.sh:145` wipes at the next run's start. `requirements` mode exits the same way (`:504–506`). The deliberate design "plan mode writes no run record" (correct — it protects commit-rate honesty) silently took the spend record down with it | `cost_report.py:29` reads only `reports/runs/*.json`; nothing else persists cost |
| **G2** | **Consumers the report cannot see.** Embedding spend (`embed-spend.json`) is read only by `vector_index` for its own daily cap — no report includes it. `make cache-probe` makes real billed calls whose cost lands nowhere durable. OpenHands-provider phases are honestly `unknown` in ledger rows, but no report line says "N tasks ran on an account we cannot meter" | grep: `embed-spend` referenced only by `vector_index.py` |
| **G3** | **"Authentic" currently means internally honest, not externally verified.** The four bases guarantee the report never lies about *what kind* of number it shows — but nothing compares the sum of `reported` rows against what the provider actually billed. Telemetry drift (a missed harvest, a double count, a CLI format change) would be invisible until the invoice | no provider usage/billing API is called anywhere |
| **G4** | **A crashed phase's spend vanishes while the provider still bills it.** A phase killed mid-call never writes its result JSON, so `budget.record()` harvests nothing — zero rows, which reads as "spent nothing" (C13) | `phase_usage()` returns zeros for unreadable input |
| **G5** | **No per-task statement.** The Cost view aggregates; `artifacts <KEY>` shows per-run spend — but "what did ticket PROJ-301 cost, in total, across its plan run, its tests run, its two retries, and the aborted attempt?" has no single answer, and G1 means part of that answer is currently *unanswerable* | by-key exists in `cost_report`, but only over run records |

---

## 2. Users

| Persona | What they get |
|---|---|
| **QA** | "What did this ticket cost me?" — one statement per task, plan run included |
| **LEAD** | A report whose totals include *every* consumer, with incompleteness stated, not hidden |
| **EM / Finance** | Reconciliation against the provider's actual bill; exportable line items |

---

## 3. Goals and non-goals

### 3.1 Goals

1. Every pipeline invocation that makes an LLM call leaves a **durable** spend
   record — whatever mode, whatever exit path.
2. One report covers **all** token consumers: phases, embeddings, probes, and
   an honest line for the unmeterable.
3. A per-task **cost statement**: everything a key cost, across all its runs.
4. **Reconciliation**: the platform's `reported` totals compared against the
   provider's own usage/billing figures, with drift alarmed.
5. Every figure keeps its basis; incomplete totals say so.

### 3.2 Non-goals

- **Not** a new metering mechanism. Harvesting the provider's own result
  telemetry stays the method; we do not count tokens ourselves.
- **Not** inventing numbers for the unmeterable. OpenHands-delegated spend
  stays `unknown`; a crashed phase's spend stays `unrecorded` (G4) — both
  *counted and named*, never estimated into the total. The iron rule outranks
  completeness.
- **Not** changing "plan mode writes no run record." That invariant protects
  commit-rate honesty and stays. The fix is a spend ledger *beside* run
  records, not a run record with a special flag someone will forget to filter.
- **Not** per-user chargeback or quotas. Attribution is per key/run/phase;
  who-ran-it stays what the audit log already records.

---

## 4. Epic A — No LLM call without a durable record (G1, G4)

### A1. The spend ledger

**Requirement.** THE SYSTEM SHALL persist, for every pipeline invocation that
ran ≥1 LLM phase, a spend record surviving the invocation — regardless of mode
(`pr|jira|plan|tests|requirements`) and exit path (success, plan-draft stop,
exit 65 clarification, exit 77 budget abort, phase failure).

**Acceptance criteria:**

- **A1.1** — A ledger entry `reports/costs/<RUN_ID>.json` SHALL be written by
  an exit-path flush (shell `trap`, so the draft-stop and abort paths are
  covered by construction, not by remembering to add a call at each `exit`).
  Rows carry: run id, mode, key, phase, provider, model, basis, the four token
  counts, turns, cost. Content is `out/cost.tsv`'s — the flush makes durable
  what metering already produced.
- **A1.2** — `cost_report` SHALL read the **union** of ledger entries and
  run-record spend blocks, deduplicated by (run id, phase) — and the
  no-double-count property SHALL be pinned with a fixture where both sources
  carry the same run. The prior PRDs' single-count rule applies: a total that
  counts one call twice is a lie in the flattering direction.
- **A1.3** — Scorecard, commit-rate and every run-metrics consumer SHALL be
  unaffected: they read run records, the ledger is not one, and a pin SHALL
  assert `plan`-mode invocations still produce **no** run record while now
  producing a ledger entry. (The G1 defect was one invariant silently riding
  another; the fix must not reverse the coupling.)
- **A1.4** — The ledger is a state store and inherits the three engineering
  rules as ACs, not advice: it lives under the volume-mounted `reports/` tree;
  mutations go through `fs_lock` and join the unlocked-RMW pin; it ships with
  `AIQE_COSTS_DIR` honoured from day one, redirected by conftest, covered by
  the class-level estate-leak pin. It joins the state bundle (a migration that
  loses the cost history loses the answer to finance's first question) and
  `make prune`'s retention (same KEEP policy as run records).
- **A1.5** — WHEN a phase started but no result JSON exists (crash, kill,
  timeout — G4), the ledger SHALL carry a row for it with basis
  **`unrecorded`**: tokens unknown, cost unknown, never zero. The provider
  billed *something*; "0" claims we know it was nothing. Totals that include
  unrecorded rows render the existing "**this total is incomplete**" banner,
  naming the runs affected.

### A2. Proof of coverage

- **A2.1** — The eval SHALL drive **all five modes** plus the abort paths (65,
  77, mid-phase kill) against the mock estate and assert a ledger entry exists
  for each — measured the way mode coverage was measured before (instrumented
  sweep), because "every exit path" claimed without enumeration is how G1
  happened.

---

## 5. Epic B — One report, every consumer (G2)

- **B1.1** — Embedding spend SHALL appear in `cost-report` as its own section
  (daily rows from `embed-spend.json`; basis per its data — estimated when
  priced from config, never silently $0). The embedding cap stays enforced
  where it is; this is reporting, not a second enforcement point.
- **B1.2** — `make cache-probe` (real billed calls by design) SHALL flush its
  spend through the same ledger path, attributed `probe`, so a measurement run
  is visible as what it cost.
- **B1.3** — The report SHALL carry an **unmeterable line**: N phases/tasks ran
  via providers whose spend cannot be metered (OpenHands basis `unknown`),
  with N counted. Absence of a number is information; absence of the *line*
  would be C13.
- **B1.4** — The by-provider rollup and per-basis counts SHALL extend to the
  new sections unchanged — one rendering of "what kind of number is this,"
  not a second one.

---

## 6. Epic C — The per-task statement and the audited report (G3, G5)

### C1. Cost statement per task

**Requirement.** `make cost-statement KEY=…` (+ `GET /api/cost-statement`, a
panel beside the key's artifacts) SHALL answer: everything this key cost.

- **C1.1** — Rows: every invocation for the key — plan run, requirements run,
  tests run, retries, failed and aborted attempts — each with per-phase tokens
  (in/out/cache-read/cache-created), turns, provider, model, basis.
- **C1.2** — Totals SHALL be **per basis, never blended**: `reported` dollars,
  `estimated` dollars (~), local tokens, simulated (~), plus counts of
  `unknown` and `unrecorded` rows. One blended number would be exactly the
  mixed-basis figure the existing per-basis count exists to prevent.
- **C1.3** — Export: `FORMAT=csv|md` to `reports/exports/` (the existing
  export path), one line item per phase — the shape finance ingests.

### C2. Reconciliation against the provider's bill

**Requirement.** WHERE an admin/usage credential is configured, THE SYSTEM
SHALL periodically compare its summed `reported` spend against the provider's
own usage/cost API for the same window, and alarm on drift.

- **C2.1** — Opt-in via Settings (`ANTHROPIC_ADMIN_KEY`, write-only like every
  secret); `make cost-reconcile [DAYS=N]`, and a `make maintain` step (subject
  to maintenance's ok/DEGRADED/failed discipline — an unreachable billing API
  is `DEGRADED`, named, never fatal, and never `ok`).
- **C2.2** — Scope honesty: only `reported`-basis rows are reconcilable, and
  the output SHALL say what fraction of total spend that is. An estate running
  mostly estimated/local/simulated gets "reconcilable: 12% of recorded spend"
  — not a false green check over the whole report.
- **C2.3** — Drift beyond org-config `budgets.reconcile_drift_pct` (default
  10%) SHALL notify via the Notify port, naming both figures, the window, and
  the two likely causes (missed harvests → platform under-reports; other
  workloads on the same API key → provider figure includes non-platform spend).
  The second cause is why drift is an *alarm to investigate*, never an
  auto-correction: the provider's number includes everything the key did, and
  "correcting" our ledger to match would import someone else's spend.
- **C2.4** — A reconciliation that could not run (no credential, API down)
  SHALL render as `not reconciled` — its own state, distinct from "reconciled,
  no drift" (C13). The Cost view badge has three states, not two.

---

## 7. Success metrics

| # | Metric | Baseline | Target | Method |
|---|---|---|---|---|
| M1 | Pipeline invocations with ≥1 LLM phase leaving a durable spend record | **plan/requirements: 0%** (G1); pr/jira/tests: 100% | **100% across all five modes and abort paths** | A2.1 instrumented sweep in `make eval` |
| M2 | Token consumers visible in the report | phases only | phases + embeddings + probes + unmeterable line — enumerated, and the enumeration is the pin | report fixture asserting each section exists |
| M3 | Double-count rate under the union | n/a (new) | 0, pinned with a both-sources fixture | A1.2 pin |
| M4 | Reconciliation drift (reported-basis, window-matched) | unmeasurable today | <10% once parity auth unblocks real runs; until then the metric renders `not reconciled` | C2, gated on credentials — **not** on this PRD |
| **Guardrails** | Report generation time; run wall-clock (the flush is a file copy, not a computation); no change to any run-metrics figure (A1.3 pin) | current | no regression | scorecard + timing in eval |

Mock-mode figures remain simulated and labelled; M1–M3 are mechanics and
provable in mock; M4 is a real-money property and says so.

---

## 8. Delivery plan

| Slice | Scope | Flag / knob | Exit criteria |
|---|---|---|---|
| **S1 — Ledger + flush** | A1 | `AIQE_COST_LEDGER` (default **on** — recording spend is not an experiment; off exists for isolation only) | trap-based flush on all exit paths; A1.3/A1.4 pins; prune + bundle wired |
| **S2 — Coverage proof** | A2 | — (eval) | five-mode + abort-path sweep green; M1 at 100% |
| **S3 — Union report + statement** | A1.2, C1 | — | dedupe pinned; statement CLI/API/panel; CSV export |
| **S4 — All consumers** | B1 | — | embeddings/probe/unmeterable sections; per-basis rendering unchanged |
| **S5 — Reconciliation** | C2 | `ANTHROPIC_ADMIN_KEY` present = on | three-state badge; drift alarm through Notify; maintenance step DEGRADED-capable |

---

## 9. Risks

| # | Risk | L | I | Mitigation |
|---|---|---|---|---|
| R1 | Union double-counts a run present in both sources | M | H | dedupe on (run id, phase), pinned with a both-sources fixture (A1.2) — this is the whole reason S3 is its own slice |
| R2 | The trap flush itself fails silently on some exit path | M | H | A2.1 enumerated sweep; the flush failure prints and events (`event_log`), never swallowed |
| R3 | Ledger growth | M | L | prune with run-record KEEP policy; entries are small JSON |
| R4 | Reconciliation reads an API key shared with non-platform workloads and "drift" is just other usage | H | M | C2.3 names it as the likely cause in the alarm; reconciliation never auto-corrects |
| R5 | A sixth estate-leak: the test suite writes real ledger entries | M | M | A1.4: isolation knob + conftest + class-level pin from the first commit — the five prior leaks are why this is an AC, not a review finding later |

---

## 10. Open questions

| # | Question | Owner | Needed by |
|---|---|---|---|
| Q1 | Retention: same KEEP as run records, or longer (finance may want quarters)? | EM + Finance | S1 |
| Q2 | Should the statement roll a PR key and its discovered ticket's key into one task view once fused-context ships? | Product | S3 |
| Q3 | Reconciliation provider scope: Anthropic first; OpenAI-compatible usage APIs vary — per-adapter `usage` verb, or Anthropic-only until asked? | EM | S5 |

---

## 11. Constraints (inherited, non-negotiable)

The iron rule (simulated labelled, savings from simulation print n/a, unknown
never 0), the four bases never blending, C13's distinct not-established states
(`unrecorded`, `unknown`, `not reconciled` are all load-bearing here), and the
three engineering rules for any new store (placement, locking, isolation —
A1.4). The prior PRDs' single-count rule extends to this union: **every avoided
or spent token is counted exactly once, in exactly one section.**

---

## Appendix A — Worked example

PROJ-301, plan-first, one retry:

```
make cost-statement KEY=PROJ-301

PROJ-301 — 4 invocations, 2026-08-05..06

  run 1754401xx-812  plan     analyze     sonnet   reported   12.4k in / 1.1k out / 9.8k cached   $0.061
                              testplan    sonnet   reported   18.2k in / 2.4k out / 14.1k cached  $0.094
                              adversary   sonnet   reported    9.1k in / 0.8k out                 $0.041
                              arbiter     sonnet   reported   11.0k in / 1.9k out                 $0.058
  run 1754402xx-990  tests    testdata    haiku    reported    …                                  $0.012
                              generate    sonnet   reported    …                                  $0.171
                              validate    haiku    reported    …                                  $0.019
                              (gate — no LLM)
  run 1754403xx-104  tests    ABORTED exit 77 — testdata recorded, generate UNRECORDED (1 row)
  run 1754404xx-233  tests    …                                                                   $0.198

  reported total:        $0.654
  unrecorded phases:     1   (run 1754403xx-104 generate — provider billed, size unknown)
  ** this total is incomplete **
  reconciliation:        not reconciled (no ANTHROPIC_ADMIN_KEY configured)
```

Before this PRD, run 1 — the plan authoring, the part the human actually
reviewed — appeared in no report at all, and the aborted run's generate phase
read as free. The statement's honesty lines are the feature: what is known is
itemised, what is unknown is counted, and nothing unknown pretends to be zero.
