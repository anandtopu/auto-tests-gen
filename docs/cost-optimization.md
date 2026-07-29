# LLM cost, caching and reuse

How this platform keeps test-plan and E2E-test generation affordable, and the
measurements behind each decision. Read `docs/architecture.md` §5.8 first — the phase
chain and budget guard are assumed here.

## 1. Where the money actually went

Measured against the real estate, not estimated:

| Context file | Size | ≈ tokens |
|---|---|---|
| `AGENTS.md` | 15.1 KB | ~3,775 |
| `out/repo-conventions.md` | 1.6 KB | ~400 |
| `out/catalog-slice.jsonl` | 2.2 KB | ~540 |
| `out/coverage-gaps.md` | 0.5 KB | ~120 |

`AGENTS.md` alone is fed to **six** authoring phases on Workflow B (analyze, testplan,
planadversary, planarbiter, testdata, generate) — **22,650 input tokens of identical
bytes per run**, before a single turn of conversation. Phases run up to `max_turns`
(8–25) and each turn re-sends the accumulated context, so the worst case for that one
file is ~283,000 input tokens in a single run.

Three structural problems produced that bill:

1. **Every phase paid the authoring tier.** `models:` named only four phases and
   `run_phase.sh` falls back to the `generate` model for anything unlisted — so
   *eight of ten* phases ran on Sonnet, including `validate`, `critic`, `testdata` and
   `triage`-grade extraction work.
2. **The prompt prefix was unique per run.** `{{KEY}}` was substituted throughout the
   prompt template before sending, putting a run-specific value within the first few
   hundred tokens. No provider-side prompt cache can hit a prefix that changes every
   run — the largest, most repetitive part of every message was structurally
   uncacheable.
3. **Identical work was redone.** Re-running a plan for an unchanged ticket against
   unchanged repos re-authored it from scratch, at full price.

## 2. What changed

### 2.1 Deliberate model tiers (largest immediate cut)

Every phase now names its tier, and the fallback that silently promoted work to Sonnet
is gone — a test fails if any phase is unlisted.

| Tier | Phases | Why |
|---|---|---|
| Haiku | `triage`, `analyze`, `testdata`, `critic`, `validate`, `resolve*` | bounded, structured jobs: classify a diff, extract behaviours from a ticket, emit fixtures from a fixed schema, score advisory quality, run specs and repair narrowly |
| Sonnet | `testplan`, `planadversary`, `planarbiter`, `generate` | judgement-grade. The plan is what a human signs off; the adversary's entire value is catching what a competent author missed, and cheap models agree too easily; `generate` writes the code that gets committed |
| Opus | `escalate` | only after two failed generate attempts |

**5 of 10 phases moved off the authoring tier**, up from 1.

### 2.2 Cache-ordered prompt assembly

`run_phase.sh` now sends the prompt template **verbatim** and appends a `RUN
PARAMETERS` block at the end carrying `KEY` and `TARGET_REPO`. The result: prompt +
shared context form a byte-identical prefix across runs of the same phase, and the
run-specific bytes sit where they cannot invalidate it.

This is a precondition for provider-side prompt caching rather than a saving on its
own — but without it, no such caching is possible at all.

The same discipline governs `engine/lib/agent_context.py` for OpenHands conversations:
protocol → estate → key state → ticket → requester note, most-stable-first, with a test
forbidding timestamps or run ids above the ticket block.

### 2.3 Content-addressed phase reuse

`engine/lib/phase_cache.py`. The key is the **whole input**:

```
sha256( phase · model · prompt template · every context file's content · artifacts )
```

Change one byte of the ticket, of `AGENTS.md`, of the prompt, or the model tier, and
the key changes and the phase runs for real. There is no TTL to tune and no
invalidation to forget — the only kind of cache worth having in a pipeline whose output
gets committed to real repositories.

A hit restores the contract **and the phase's artifacts** (`testplans/<KEY>.md`,
`testdata/<KEY>/`), so it reproduces the phase's full effect rather than just its JSON.

**`generate` and `validate` are excluded by construction.** Their product is not the
contract — it is files written into `workspace/tests/<repo>` and the git state the gate
then inspects. Replaying a contract without re-writing those files would hand the gate
a clean tree and a green report for work that never happened. `CACHEABLE` is an
allow-list, and adding a phase to it asserts that its contract plus its declared
artifacts *are* its entire product.

```bash
make cache-stats     # LLM calls avoided, by phase
make cache-clear     # drop every cached result
```

`AIQE_PHASE_CACHE=0` disables it for a run. Storage is `reports/phase-cache/`
(gitignored, prunable).

### 2.4 Reuse that already existed

Worth naming, because the cheapest call is the one never made:

- **Routing is deterministic.** `engine/phases/resolve.py` is rules-first from the
  registry — no model runs unless confidence falls below the threshold.
- **Plan-first mode stops before generation.** A rejected plan costs one plan, not a
  plan plus a full generate/validate/gate cycle.
- **The approved-plan guard.** `agent_context` blocks blind re-authoring of an approved
  plan — which would have cost a full plan phase *and* destroyed a human sign-off.
- **Per-repo fan-out is scoped, not duplicated.** Each agent gets only its own repo's
  conventions, so N repos cost N *smaller* calls rather than N full-estate ones.
- **The budget guard is real.** `engine/lib/budget.py` checks cost and wall-clock
  *before* every phase; an over-budget run aborts with exit 77 and never reaches the
  gate, so a runaway overshoots by at most one phase.

## 3. Still on the table

Ranked by expected saving ÷ effort. None are implemented; each is a real option.

| # | Idea | Expected saving | Cost / risk |
|---|---|---|---|
| 1 | **Scope `AGENTS.md` per run.** Phases receive the whole estate (15 KB) when they need the resolved repos' slice. A generated per-run digest would cut the dominant static payload by ~70% | Large — it is the biggest single input | Medium. Must not hide surface a phase legitimately needs; pin with a test that the resolved repos always survive the trim |
| 2 | **Cap `max_turns` by observed usage.** `generate` allows 25 turns; if real runs finish in 6, the ceiling is buying nothing but risk | Medium | Low, but needs real-run telemetry first — see §4 |
| 3 | **Skip phases that cannot change anything.** `critic` on a run with zero generated tests; `planadversary` on a plan with zero scenarios | Small but free | Low |
| 4 | **Batch the fan-out for small diffs.** Below a diff-size threshold, one call for N repos may beat N calls | Situational | Medium — reintroduces the cross-wiring risk the fan-out removed. Needs a guard |
| 5 | **Semantic plan reuse.** Reuse a plan from a *similar* ticket, not just an identical one | Potentially large | High. Similarity is a judgement call; a wrong reuse produces a confidently incorrect plan. Only worth it with a human diff step |

## 4. The honest caveat

Every figure in §1 is a **token count**, measured from real file sizes and the real
phase chain. None is a **dollar figure from a real run**, because `make parity-pr` /
`parity-jira` remain blocked on Claude CLI auth (REVIEW.md open item 5). The scorecard's
"cost per run" reads `n/a` for the same reason: the previous `$0.20` came only from
`AIQE_MOCK_PHASE_COST` simulation in tests.

So: the model-tier change and the phase cache are sound on structural grounds and are
pinned by tests, but the **saving has not been measured end to end**. The first real
parity run should record before/after cost per run, and that number — not this document
— is what should be quoted to anyone paying the bill.
