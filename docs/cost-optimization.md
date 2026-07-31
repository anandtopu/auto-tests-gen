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

### 2.5 Spend telemetry (cost-reduction story 1.1/1.2, shipped)

Every phase's spend now lands in the run record: `phases[].spend = {model,
input_tokens, output_tokens, cache_read_tokens, cache_creation_tokens,
turns_used, max_turns, cost_usd, simulated}` — harvested from the `claude -p`
result JSON the pipeline already saved (`usage`, `num_turns`, `total_cost_usd`),
via the budget ledger. `make cost-report [DAYS=N]` (and `GET /api/cost-report`,
the Overview "LLM spend" tile, a team-report line) rolls it up by workflow, key,
phase, and model tier, with per-phase turn calibration (p50/p95 vs ceiling) and
prompt-cache hit rates. Simulated figures are always labelled — `~` on CLI
tables, "simulated" in reports — and savings estimates print `n/a` until at
least one measured run exists. OpenHands launches record their payload size
(`message_chars`) so the separately-billed conversation cost is at least
attributable per launch.

## 3. Still on the table

Ranked by expected saving ÷ effort. Items 1, 3 and 5 SHIPPED with the
cost-reduction backlog (see §5); what remains:

| # | Idea | Expected saving | Cost / risk |
|---|---|---|---|
| 1 | ~~Scope `AGENTS.md` per run~~ — **shipped** (§5, retrieval-scoped context; measured 58% avg size reduction on the benchmark) | — | resolved-repo survival pinned |
| 2 | **Cap `max_turns` by observed usage.** The telemetry now exists (`cost-report` prints p50/p95 + a suggested ceiling); applying it to org-config stays a human act | Medium | Low — needs measured runs first (§4) |
| 3 | ~~Skip phases that cannot change anything~~ — **shipped** (§5, no-op skips) | — | — |
| 4 | **Batch the fan-out for small diffs.** Below a diff-size threshold, one call for N repos may beat N calls | Situational | Medium — reintroduces the cross-wiring risk the fan-out removed. Needs a guard |
| 5 | ~~Semantic plan reuse~~ — **shipped** (§5, behind `AIQE_PLAN_REUSE`, default off until the quality eval) | — | human diff step built in |

## 5. The retrieval/reuse stack (cost-reduction backlog, shipped)

Built as 8 slices against `docs/cost-reduction-stories.md` (designs in
`docs/cost-reduction-architecture.md`). One paragraph per layer; every
mechanism has a kill switch (Settings → "Cost levers") and its pins.

- **Telemetry** (`cost_report.py`): per-phase spend blocks in every run record,
  harvested from the CLI's own usage JSON. `make cost-report`, a dashboard
  Cost view, an Overview tile, team-report line. Simulated figures always
  labelled; savings print `n/a` until a measured run exists.
- **Knowledge chunks** (`knowledge_chunks.py`): the estate chunked into
  addressed units (repo-surface / guidance / exemplar / spec / catalog /
  scenario / testdata) — derived data, byte-deterministic, rebuilt with
  AGENTS.md.
- **Vector index** (`vector_index.py` + the Embed port, `docs/adr/embeddings.md`):
  SQLite + pure-python cosine; sha-skip refresh (unchanged corpus = zero
  embedding calls); daily spend cap; corruption → quarantine + rebuild.
  Unconfigured → TF-IDF everywhere, silently.
- **Retrieval-scoped context** (`context_scope.py`): three-tier per-run
  assembly (must-keep → deterministic overlap → semantic fill) with an audit
  manifest of kept AND dropped chunks. Judgement phases stay on the full
  estate until the quality eval clears them. `missing_context` in a contract
  buys one full-estate retry. **Measured: 58% avg context-size reduction on
  the benchmark, retention-checked every `make eval`.**
- **Semantic reuse** (`plan_reuse.py`): a duplicate-shaped ticket adapts a
  prior HUMAN-APPROVED plan by deterministic text surgery instead of an LLM
  call; adversary still runs; lands as draft with visible provenance
  everywhere. Exemplars rank semantically (legacy penalty first); testdata/
  testplan contexts pull PRIOR ART under an explicit data-framing heading.
- **Spend controls**: no-op phase skips; per-workflow budget envelopes with a
  queue-intake warning; a degradation ladder (60% → cheap tier for
  non-judgement phases, 80% → halved context budgets, 100% → the existing
  exit-77 abort). Judgement phases never downgrade.
- **Prompt caching**: `make cache-probe` measures whether provider caching
  engages on our prefix shape before anyone builds a fallback; `cost-report`
  tracks per-phase hit rates against an optional floor.
- **Ops**: `make cost-baseline` freezes measured medians (refuses simulated);
  `make maintain` runs the chunk rebuild, index refresh and the cost
  regression alarm nightly; the vector index is bundle-excluded derived data
  (`make index-rebuild` restores it after an import).

The measured-vs-simulated rule from §4 governs all of it: the 58% context
reduction and the sha-skip zero-call refresh are mechanical facts; every
dollar figure remains `n/a`/`~`-labelled until the parity runs land.

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
