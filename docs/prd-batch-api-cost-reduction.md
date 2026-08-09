# PRD — Message Batches API as a cost-reduction provider

**Status:** slices 1 + 1b BUILT (`adapters/llm/batch.sh`, 17 pins); slices 2-3 proposed
**Author:** AI QE Platform, Product
**Date:** 2026-08-09
**Related:** `docs/multi-llm-providers.md` (the LLM Runner port this builds on),
`docs/cost-optimization.md`, `docs/efficiency-review.md`, `REVIEW.md` item 5
(the parity-auth blocker that gates every measurement claim below)

---

## 1. The short answer

**Yes, the Message Batches API can cut LLM spend roughly in half — but only for
about half of this platform's LLM calls, and only where a delayed answer is
acceptable.** Three findings shape the entire design, and each was verified
rather than assumed:

1. **Batch cannot run our agentic phases.** `generate`, `validate` and
   `reviewrepair` drive a client-side tool loop in `workspace/tests/<repo>`. A
   batch request is a single Messages call: the model can *return* `tool_use`
   blocks, but every turn that needs a client tool result requires a **new batch
   submission**. At roughly an hour per batch, a 25-turn `generate` phase is not
   a viable batch workload. Batch is therefore a **completion-class provider** —
   the exact class this codebase already models for `ollama`.

2. **There is no Claude Code batch flag.** `claude --help` (v2.1.220, checked on
   this machine) contains **zero** occurrences of "batch". The Batch API is a
   Messages API feature reached over HTTP with an API key. `claude -p` cannot
   submit or drain a batch, and no flag will make it do so. This directly
   answers the "claude code usage flag" requirement: the flag can exist and
   *route eligible phases away from the CLI to the batch path*, but it cannot
   turn a `claude -p` invocation into a batched one. See §4.

3. **Batch is asynchronous by definition.** Most batches finish in under an
   hour; the hard expiry is 24 hours. A PR gate that answers in an hour is a
   different product from one that answers in ninety seconds. Whether that is
   acceptable is a **customer decision**, not ours, so it is exposed as
   configuration rather than decided here (§5).

**Measured scope on this estate:** of 2143 recorded phase calls,
**1104 (51.5%) are in batch-eligible phases** and 1039 are agentic. That is a
real count of the workload's shape.

**What is NOT measured:** the *spend* share of those calls. This estate's cost
report is **99% simulated**, so any dollar figure derived from it would be a
number invented from mock runs — the one thing `docs/cost-optimization.md`
forbids. The eligible phases skew cheap (`triage`, `critic` on the haiku tier)
and the agentic phases skew expensive (`generate` on the authoring tier), so
**51.5% of calls is an upper bound on the call mix and almost certainly an
overstatement of the money.** Sizing it honestly requires `make parity-pr` /
`make parity-jira` (blocked on provider auth, REVIEW.md item 5) followed by
`make cost-baseline`. §9 makes that the gate on the headline claim.

---

## 2. Verified API facts

From the Message Batches API documentation, retrieved 2026-08-09:

| Property | Value |
|---|---|
| Cost reduction | **50%** on input and output |
| Batch size limit | **100,000 requests or 256 MB**, whichever comes first |
| Typical latency | most batches complete **in under 1 hour** |
| Hard expiry | **24 hours**; unprocessed requests end as `expired` and are **not billed** |
| Result retention | **29 days** after creation |
| Retrieval | **poll** batch status, then download results once all requests have ended |
| Result ordering | **not guaranteed** — results may return in any order, so `custom_id` is mandatory |
| Per-request statuses | `succeeded`, `errored`, `canceled`, `expired` |
| Tool use | supported in the request, including server tools |
| Prompt caching | supported, and **the discounts stack**; the 1-hour cache duration is recommended because batches routinely exceed the 5-minute TTL |
| Models | all active models |
| `max_tokens` | must be ≥ 1 (cache pre-warming with `max_tokens: 0` is not supported in batches) |

Two of these drive design decisions later: **unordered results** (§6.2) and
**`expired` is a distinct outcome that is never billed** (§7).

---

## 3. Phase eligibility

The platform already has the concept this needs. `engine/lib/llm_runner.py`
declares:

```
AGENTIC_PROVIDERS = ("claude", "codex", "mock")
AGENTIC_PHASES    = ("generate", "validate", "reviewrepair")
```

and refuses a completion-only provider on an agentic phase **at config time,
naming the fix**. A batch provider inherits that refusal for free — no new
guard, no new failure mode.

| Phase | Class | Batch? | Why |
|---|---|---|---|
| `triage` | completion | ✅ | emits a JSON contract; no workspace writes |
| `analyze` | completion | ✅ | same |
| `testplan` | completion + derived writes | ✅ | `derived_writes.py` already renders `testplans/<KEY>.md` from the contract |
| `planadversary` | completion | ✅ | read-only by constitution — it may not edit the plan |
| `planarbiter` | completion + derived writes | ✅ | same renderer path as `testplan` |
| `testdata` | completion + derived writes | ✅ | fixtures carry content in the contract |
| `critic` | completion | ✅ | advisory, read-only, never gates a commit (C2) |
| `reviewer` | completion | ✅ | read-only; its verdict is consumed before the gate (C14) |
| `resolve` | deterministic | n/a | no LLM call |
| `generate` | **agentic** | ❌ | writes specs into the test repo; multi-turn tool loop |
| `validate` | **agentic** | ❌ | executes tests and iterates |
| `reviewrepair` | **agentic** | ❌ | edits existing generated files |

`derived_writes.py` is what makes the plan family work on a completion provider
today, and it is the reason this proposal is small: **the artifact
materialization problem is already solved.**

---

## 4. The "Claude Code flag" — what it can and cannot mean

The requirement is that the feature "support the claude code usage flag so that
this BATCH API is applicable to CLAUDE CODE integration". Stated precisely:

- **It cannot make `claude -p` batch.** No such flag exists in the CLI (verified
  above). Anyone who ships a `--batch` passthrough would be shipping a no-op.
- **It can route around the CLI.** The flag selects a *different adapter* —
  `adapters/llm/batch.sh` — which speaks the Messages Batches HTTP API for
  eligible phases, while `claude -p` continues to serve the agentic phases.
  Within one run, `generate` goes through the CLI and `testplan` goes through
  batch. That is already how `llm.phase_providers[phase]` works.

**The auth consequence must be stated, not discovered.** `claude -p` commonly
authenticates via a Claude subscription (`claude login`); the Batch API requires
an `ANTHROPIC_API_KEY` with its own billing. Turning batch on for an estate that
has only CLI auth will fail at the first submission. Per C13, that is a
**config-time refusal naming the missing key**, never a silent fallback to the
paid synchronous path — which would be the worst outcome, since the user enabled
this feature specifically to spend less.

---

## 5. Where the savings actually are

Batch is not a switch that makes the current product cheaper without changing
it. It is worth having in three places, in descending order of value:

> **Correction (2026-08-09, after slice 1 shipped).** An earlier draft of this
> section called the spooled fan-out "the real win" and implied the saving grew
> with batch size. It does not. The pricing page states that **all** Batches API
> usage is charged at 50% of standard prices — the discount is a property of
> *using* the API, not of how many requests share a batch, and a batch of one
> gets the full 50%. **Slice 1 therefore already captures 100% of the price
> saving.** Slice 2 buys *throughput and wall-clock*, which is still worth
> having (40 tickets as 40 sequential one-request batches could take 40 hours;
> as one batch it is about one) — but it must be justified on latency, not
> money. §8 is corrected to match.

### 5.1 Bulk plan authoring (the strongest case)
"Generate test plans for every ticket in release 24.3" is 40 tickets ×
(`analyze` + `testplan` + `planadversary` + `planarbiter`) ≈ 160 completion
calls that nobody is waiting on. This is *exactly* what the Batch API is for,
and the platform already has the surfaces: the work queue
(`engine/lib/work_queue.py`), plan-first mode (`pipeline.sh plan <KEY>`), and
fetch-by-release in the dashboard. **Recommended as slice 2 and the headline
use case.**

### 5.2 Estate-wide background analysis
Catalog bootstrap's `claude -p` classify stage, coverage-gap analysis, and
nightly `make maintain` work are all deferred by nature. No user is blocked.

### 5.3 PR triage, *if* the org accepts delayed feedback
`triage` is eligible by capability. Whether a PR may wait an hour for its tests
is a policy question with different answers at different companies. Exposed as
configuration (§6.3); **defaulted off**, because silently turning a 90-second
feedback loop into a 60-minute one would be a regression disguised as a saving.

---

## 6. Design

### 6.1 `adapters/llm/batch.sh` — a completion-class provider

Verbs, matching the existing port contract (`run_phase`, `capabilities`,
`check`, `tool_policy`):

- `capabilities` → `completion`. Agentic phases are refused at config time by
  the existing `check_model_mapping` / capability validation, with the fix named.
- `tool_policy <allowed_tools>` → must answer no wider than the requested
  policy. Batch grants **no client tools**, so a read-only policy stays
  read-only and the conformance check passes trivially.
- `run_phase` → submits, polls, returns the **normalized result JSON** every
  adapter returns (`result` / `usage` / `num_turns` / `total_cost_usd` +
  `provider` / `model`), so telemetry stays provider-agnostic.
- The completion-only prompt addendum ("you have no write tools; inline the
  content") is appended **inside the run-parameters block**, after the cacheable
  prefix — the existing rule, so prefix caching is unaffected.

**Model IDs are configured, never guessed:** `llm.models_by_provider.batch`,
enforced by the existing `check_model_mapping`, so a claude-namespace id cannot
leak into a provider that would reject it layers below the switch.

### 6.2 Two execution modes

**Mode A — blocking single-request batch (slice 1).** One phase, one batch, poll
until done. Gets the 50% discount with no new orchestration, and reuses every
existing seam. Honest about its cost: the phase blocks for minutes to an hour.
Only sane for queue-drained and background work.

**Mode B — spooled fan-out (slice 2, the real win).** A `batch spool` collects
requests across many keys, submits **one** batch, and drains results into plan
drafts. `custom_id` is `<run_id>:<key>:<phase>` — mandatory, because **results
may return in any order**. New surface:

```bash
make batch-plan RELEASE=24.3        # spool + submit
make batch-status                   # what is in flight, and since when
make batch-drain                    # materialize completed results
```

Drain reuses `derived_writes.py` and `spec_store` unchanged, so a plan authored
through batch is byte-identical in shape to one authored synchronously.

### 6.3 Configuration

Layering follows the existing precedence (`aiqe.properties` < `.env` < explicit
environment):

```yaml
llm:
  batch:
    enabled: false                  # master switch, default OFF
    phases: [analyze, testplan, planadversary, planarbiter, testdata, critic]
    max_wait_minutes: 90            # give up waiting; never silently reroute
    on_unavailable: refuse          # refuse | defer   (never: fall back to paid sync)
    include_pr_triage: false        # §5.3 — the org's latency decision
```

with `AIQE_LLM_BATCH=1` as the env override and a Settings-page section, matching
how every other provider knob is exposed.

**Why `triage` and `reviewer` are eligible but not in the default list.** Both
run *inside* a live run that someone is waiting on. `triage` opens the PR path
(§5.3), and `reviewer` sits immediately before delivery — under
`review: require` it can refuse the run at exit 78, so batching it stalls the
decision that releases the work. They are eligible by capability and excluded by
*latency*, which is a different reason and is worth keeping separate: a future
estate running everything nightly can safely add both.

`on_unavailable: refuse` is deliberate and follows **constitution C12**: a
provider switch is explicit configuration, with **no silent fallback** to a
different (possibly paid) provider. An operator who enabled batch to halve their
bill must never discover they paid full price because a submission failed.

---

## 7. Cost accounting — where this gets subtle

The platform already distinguishes four cost bases that never cross: `reported`,
`estimated`, `local`, `simulated`. Batch needs care in two places.

**The 50% is applied, not observed.** A batch result returns token *usage*, not
dollars. So batch spend is **`estimated`** — priced from org-config `pricing:`
with a documented `batch_discount: 0.5` multiplier — and renders with the `~`
prefix like every other estimate. Recording it as `reported` would assert a
precision we do not have. A provider/model with no `pricing:` entry stays
`unknown`, never 0, because a 0 understates a real bill (the R1 defect this
codebase already fixed once).

**`expired` and `canceled` are not failures, and not successes.** Both are
explicitly **not billed**. Per C13 each gets its own state:

| Outcome | Meaning | Spend |
|---|---|---|
| `succeeded` | the phase ran | estimated from tokens × 0.5 |
| `errored` | the request failed | per API response |
| `expired` | 24h passed; **never sent to the model** | **$0, and said so** — not "the phase produced nothing" |
| `canceled` | cancelled before dispatch | **$0, and said so** |
| *waiting* | still in flight | **not yet known** — never rendered as $0 |

A batch still in flight must never be reported as a completed phase costing
nothing. That is the same defect class as the unpriced-provider bug (R1), where
27.5M tokens reported "$0.00, within budget".

**Budget enforcement changes shape.** The exit-77 ceiling and the degradation
ladder are checked *before each phase*. With batch, spend is known only after the
drain, so a spooled batch can commit spend that no pre-phase check saw. Slice 2
must price the spool **at submission time from token estimates** and refuse to
submit a batch that would breach the envelope — otherwise the ceiling silently
stops applying to exactly the workload designed to be large.

---

## 8. Rollout

| Slice | Content | Value |
|---|---|---|
| 1 | `adapters/llm/batch.sh`, capabilities/tool_policy/check, blocking mode, `pricing:` + `batch_discount`, cost basis, config + Settings, conformance | 50% on eligible phases for queued/background work |
| 2 | Spool + `make batch-plan/status/drain`, `custom_id` correlation, envelope pricing at submit | **throughput, not extra discount** — 40 tickets in ~1h instead of ~40 sequential batches (see the §5 correction) |
| 3 | Wire the bootstrap classify stage and `make maintain` analysis to batch | background spend halved |
| 4 | Optional PR-triage batching behind `include_pr_triage` | org-by-org latency call |
| 5 | `make test-batch` adversarial suite (§9) | proof the failure modes are honest |

---

## 9. How we will know it worked (and what would make this PRD wrong)

**The headline "≈50% on half the calls" is a claim about the call mix, which is
measured, multiplied by a discount, which is documented. The share of *spend* is
unmeasured on this estate and must not be quoted until it is.** Gate:

1. Authenticate the provider and run `make parity-pr` + `make parity-jira`.
2. `make cost-baseline` to freeze measured per-phase medians.
3. Re-derive the eligible **spend** share from those medians.
4. Only then publish a savings figure — with `~`, because it stays an estimate.

If step 3 shows the eligible phases are a small share of spend (plausible:
`generate` is the authoring tier and dominates), then **slice 1 is not worth
building** and the honest recommendation collapses to slice 2 only, for bulk
authoring. This PRD should be revised, not defended.

**Adversarial suite (`make test-batch`), mirroring `make test-providers`:**

- a batch that expires reports `expired` and **$0 not billed**, never "the phase
  produced nothing";
- an in-flight batch is never rendered as a completed $0 phase;
- results returned **out of order** are correlated correctly by `custom_id`
  (deliberately shuffled in the fixture);
- batch unavailable/unauthenticated → **refusal naming the fix**, never a
  fallback to the paid synchronous provider;
- an agentic phase configured to batch is refused **at config time**;
- the batch adapter's `tool_policy` is never wider than the requested policy;
- a spooled batch that would breach the run envelope is refused at submission.

---

## 10. What this feature will not do

- It will not make `generate`, `validate` or `reviewrepair` cheaper. Those are
  agentic and stay on the CLI. Anyone expecting "half off everything" should
  read §1.
- It will not make `claude -p` batch. No flag exists.
- It will not keep PR feedback fast *and* batch it. Those are opposites; §5.3 is
  a choice, not a free lunch.
- It will not fall back to the synchronous paid path when batch is unavailable
  (C12). Failing loudly is the feature.
- It will not report a dollar saving until a measured baseline exists (§9).

---

## Appendix A — evidence

| Claim | How it was checked |
|---|---|
| 50% discount, 100k/256MB, <1h typical, 24h expiry, 29d retention, unordered results, `expired` unbilled, caching stacks | Message Batches API docs, retrieved 2026-08-09 |
| Claude Code cannot batch | `claude --help` on v2.1.220 — 0 matches for "batch" |
| Agentic vs completion split already exists | `engine/lib/llm_runner.py`: `AGENTIC_PROVIDERS`, `AGENTIC_PHASES`, config-time refusal |
| Artifact materialization already solved | `engine/lib/derived_writes.py` (testplan/planarbiter/testdata) |
| 1104 of 2143 calls (51.5%) are batch-eligible | `make cost-report DAYS=3650`, per-phase call counts |
| Spend share is unmeasured | same report: **"99% simulated"**; `parity-*` blocked (REVIEW.md item 5) |
