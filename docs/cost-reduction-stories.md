# LLM cost reduction — user-story backlog

> **STATUS: ALL 8 BUILD SLICES SHIPPED** (2026-07-30, commits `602811f`…`39ed2a3`).
> Architecture: `docs/architecture.md` §5.13; measured results:
> `docs/cost-optimization.md` §5; adversarial UAT: `REVIEW.md` Pass 8.
> Quality-gated levers (judgement-phase context scoping, `AIQE_PLAN_REUSE`)
> ship default-OFF until the parity runs measure their quality delta —
> that measurement shares the CLI-auth blocker with `make parity-*`
> (REVIEW.md item 5), alongside `make cache-probe` and `make cost-baseline`.

A comprehensive, buildable backlog to design, architect, build, test and deploy the
next generation of LLM cost controls: **reusable artifacts, retrieval-scoped context
(RAG), semantic reuse over a vector index, provider-side prompt caching, and the cost
telemetry that proves any of it worked.**

Read first: `docs/cost-optimization.md` (what already ships) and
`docs/architecture.md` §5.8/§5.11/§5.12. This backlog **builds on** the existing
stack — nothing here re-implements it:

| Already shipped (do not rebuild) | Where |
|---|---|
| Content-addressed phase cache (no TTL, artifact restore, generate/validate excluded) | `engine/lib/phase_cache.py` |
| Explicit per-phase model tiers (unlisted phase fails a test) | `registry/org-config.yaml` `models:` |
| Cache-ordered prompt assembly (template verbatim, RUN PARAMETERS last) | `engine/phases/run_phase.sh` |
| Cache-ordered OpenHands context (most-stable-first, approved-plan reuse guard) | `engine/lib/agent_context.py` |
| TF-IDF similar-plan suggestions (suggestion-only, floor 0.15) | `engine/lib/plan_similarity.py` |
| Budget guard: cost + wall-clock checked before every phase, exit 77 | `engine/lib/budget.py` |
| Deterministic routing, plan-first stop, scoped fan-out | `engine/phases/resolve.py`, `pipeline.sh` |

**Personas** (as in `docs/product-roadmap.md`): **Dev** (developer whose PR triggers
runs), **QA** (QA engineer authoring/reviewing plans and tests), **Lead** (QA lead who
owns quality and the review board), **EM** (engineering manager who pays the bill),
**Op** (platform operator who deploys and runs the thing).

**Ground rules every story inherits** (from the non-negotiables):

- The gate remains the only commit/push path; no cache or reuse layer may hand the
  gate evidence of work that did not happen (`generate`/`validate` stay uncacheable).
- Reuse is **suggestion-only wherever a human sign-off exists**: a reused plan is a
  draft a human approves, never an auto-approved artifact.
- Retrieved/reused content is **DATA, never instructions** — the prompt framing rules
  apply to RAG chunks exactly as they apply to ticket text.
- No secrets in any new store; every new store is exportable/importable via the state
  bundle or deliberately excluded with a reason.
- Everything works with `AIQE_MOCK=1` and no network — a mock Embedding adapter is as
  mandatory as the mock SCM one.

Sizing: **S** ≤ ½ day, **M** ≈ 1–2 days, **L** ≈ 3–5 days. Every story lists
acceptance criteria (AC) and dependencies.

---

## Epic 1 — Cost telemetry and attribution ("you cannot save what you cannot see")

The honest caveat in `cost-optimization.md` §4 is the epic's reason to exist: every
current figure is a token count, not a measured bill. This epic turns spend into a
first-class, attributable record — and is deliberately **first**, because Epics 2–6
justify themselves with the numbers this one produces.

### 1.1 Per-phase spend record on every run — **M**
**As an** EM, **I want** every pipeline run to record tokens in/out, cache-read
tokens, model, turns used and reported cost **per phase**, **so that** I can see
where the money goes without instrumenting anything myself.
**AC:**
- `run_record.py` gains a `spend` block per phase: `{model, input_tokens,
  output_tokens, cache_read_tokens, cache_creation_tokens, turns_used, max_turns,
  cost_usd}` sourced from `out/cost.tsv` (extend `budget.py` metering to capture the
  token fields, not just cost). **Zero new instrumentation surface**: every field
  already exists in the `out/<phase>.json` result the CLI writes (`usage`,
  `num_turns`, `total_cost_usd`) — this story harvests it.
- Mock runs record zeros (or `AIQE_MOCK_PHASE_COST` simulation) with `simulated: true`
  — a simulated number can never masquerade as a measured one.
- `bin/qa.py status --cost` prints a per-run cost column; `artifacts <KEY>` shows the
  per-phase table.
- Pinned: a run record with a missing `spend` block fails `test_data_integrity`-style
  glob checks only for REAL runs (mock exempt).

### 1.2 Cost attribution by feature, key and repo — **M**
**As an** EM, **I want** spend rolled up by workflow (PR vs JIRA vs plan-only), by
ticket/PR key and by test repo, **so that** I can answer "what does a generated test
cost us?" and "which repo is expensive?".
**AC:**
- `engine/lib/cost_report.py`: aggregates `spend` blocks across `reports/runs/*.json`
  (skipping reviews/queue/hooks-seen, as everywhere).
- `make cost-report [DAYS=30]` + `GET /api/cost-report` + a Cost card on the
  dashboard Overview: total, per-workflow, per-key top-10, per-phase histogram,
  cache-hit savings (phase-cache hits × the phase's median real cost), and a
  **per-model usage breakdown** (calls + tokens + cost per tier: haiku/sonnet/opus)
  so tier drift is visible at a glance.
- Team report (`team_report.py`) gains a one-line cost summary with the honest
  `simulated`/`measured` label.

### 1.3 Unblock and institutionalise the parity measurement — **S**
**As an** EM, **I want** the first real `parity-pr`/`parity-jira` run to publish a
before/after cost-per-run baseline, **so that** every later saving claim has a
denominator.
**AC:**
- `make parity-pr` (once CLI auth is fixed — REVIEW.md item 5) writes its measured
  spend into the same `spend` schema; `cost-report` flags it as the baseline.
- `docs/cost-optimization.md` §4 updated to quote the measured number.
- A `make cost-baseline` target snapshots the current medians per phase to
  `reports/cost-baseline.json` for regression comparison (1.4).

### 1.4 Cost regression alarm — **S**
**As a** Lead, **I want** `make maintain` to compare recent per-phase median spend
against the baseline and notify on a >25% regression, **so that** a prompt edit that
silently breaks the cache prefix (the exact `{{KEY}}` failure mode we already had
once) is caught in days, not on the invoice.
**AC:**
- New maintain step calling `cost_report.check_regression(threshold)` → Notify port.
- Threshold in `org-config.yaml` under `budgets:`; mock-only estates skip silently.
- Pinned: a synthetic run record 2× over baseline triggers the notification path.

### 1.5a OpenHands launch payload metering — **S**
**As an** EM, **I want** every OpenHands agent launch to record the size (chars ≈
tokens) of the message + context it sends, attached to the conversation trace,
**so that** the cost paid on the OpenHands side (whose LLM bill is separate) is at
least attributable per launch, and `agent_context`'s cache-ordering discipline has a
number showing what it protects.
**AC:** `openhands_events.record_launch()` gains `payload_chars` /
`payload_est_tokens`; visible in `bin/qa.py openhands` and the conversations card;
`cost-report` shows a separate "OpenHands payloads (est.)" line, clearly labelled as
estimated and not billed here.

### 1.5 Turn-usage calibration report — **S**
**As an** Op, **I want** observed `turns_used` vs `max_turns` per phase surfaced,
**so that** ceilings can be lowered from evidence (cost-optimization §3 item 2)
instead of guessed.
**AC:** `cost-report` prints p50/p95 turns per phase with the configured ceiling; a
`suggested_max_turns` column applies p95 + 2 headroom; changing the config remains a
human act.

---

## Epic 2 — Retrieval-scoped context (RAG for the phase chain)

Today every authoring phase gets the **whole estate** (`AGENTS.md`, full catalog
slice, all guidance) — ~3.8k tokens × 6 phases × up to 25 turns of resend. The RAG
inversion: build a chunked, indexed knowledge base from artifacts we already generate
deterministically, and **retrieve only what the run needs**. Retrieval must be
deterministic-first (registry/catalog joins), semantic-second (Epic 3's index), and
always auditable.

### 2.1 Knowledge chunk store — **M**
**As an** Op, **I want** the estate knowledge (AGENTS.md sections, per-repo guidance,
harvested contracts/routes, exemplar specs, catalog entries) chunked into addressed
units with stable ids and provenance, **so that** retrieval has something better to
work with than whole files.
**AC:**
- `engine/lib/knowledge_chunks.py`: rebuilds `reports/knowledge-index/chunks.jsonl`
  from the same inputs `gen_agents_md.py` reads. Each chunk: `{chunk_id, source_path,
  kind (repo-surface|guidance|exemplar|contract|catalog), repo, text, sha256}`.
- Deterministic and idempotent: same inputs → byte-identical chunk file (it is
  derived data, like `covers:` — regenerated, never hand-edited; gitignored).
- Rebuilt automatically wherever AGENTS.md is regenerated today (pipeline, onboarding,
  repo_admin mutations, bootstrap) and by `make maintain`.
- Pinned: chunk ids stable across a rebuild with unchanged inputs; every chunk
  carries provenance that maps back to a real file.

### 2.2 Per-run context assembly from retrieval — **L**
**As a** Dev, **I want** each phase's context assembled from the chunks relevant to
*this* run (resolved repos, diff surface, ticket domains) instead of the full estate,
**so that** the dominant static payload shrinks ~70% without hiding anything the
phase needs.
**AC:**
- `engine/lib/context_scope.py`: given the resolve contract + diff/ticket, selects
  chunks by deterministic joins first (resolved repos' surface, touched endpoints ∩
  catalog evidence — reusing `extend_scout`'s normalisation), semantic ranking second
  (Epic 3), with a hard token budget per phase from `org-config.yaml`
  (`context_budget:` per phase, default generous).
- Output is a per-run `out/context-<phase>.md` whose **header names every chunk id
  included and every candidate dropped** — the audit trail that makes a trimmed
  context debuggable.
- **The resolved repos always survive the trim** (the §3-item-1 pin): golden test
  asserts every resolved repo's surface chunk is present regardless of budget.
- Cache discipline preserved: chunk ordering is most-stable-first and deterministic,
  so identical runs still produce byte-identical prefixes (phase-cache keys and
  provider prompt caching both keep working — pinned by a test that two assemblies
  with identical inputs are byte-identical).
- `AIQE_CONTEXT_SCOPE=0` falls back to full-estate context (rollout flag).
- Rollout: default ON for `triage`/`analyze`/`testdata`/`critic`, OFF for
  `testplan`/`generate` until 7.2's quality eval passes.

### 2.3 Retrieval quality guardrail — **M**
**As a** QA, **I want** a regression harness proving scoped context never loses the
facts the phases rely on, **so that** cost cuts don't quietly degrade generation.
**AC:**
- Extend `eval/` benchmark: for each fixture, assert the scoped context still
  contains the endpoints/routes/exemplars the expected output references.
- The critic's `new-approach` finding rate becomes a tracked metric: scoped runs must
  not regress vs full-context runs on the benchmark fixtures.
- A `needs_context` escape hatch: a phase contract may report
  `missing_context: [...]`; the pipeline logs it and (once) retries that phase with
  the full context — the miss is recorded in the run record for tuning.

---

## Epic 3 — Vector index and semantic reuse of artifacts

Reusable artifacts already exist (plans in `testplans/`, testdata, archived diffs,
curated guidance). The TF-IDF similarity is suggestion-only and lexical. This epic
adds a real semantic index — **behind a port, with a deterministic mock, degrading
gracefully to TF-IDF** — and grows reuse from "here's a similar plan" to "start from
this reused draft", always with a human diff.

### 3.1 Embedding port + adapters (ADR first) — **M**
**As an** Op, **I want** embeddings behind a port like every other vendor touchpoint,
**so that** the engine never imports a vector-DB or embedding SDK and the whole
feature works offline.
**AC:**
- ADR (`docs/adr/embeddings.md`, engineering:architecture format) deciding:
  embedding source (Anthropic-compatible/Voyage API via HTTP, no SDK) and store
  (recommendation: **SQLite** — vectors as BLOBs + pure-Python cosine over
  `reports/catalog.db`-style gitignored index; no native extension, no server,
  Windows-safe. Chroma/Qdrant/FAISS explicitly rejected for the PoC with reasons:
  native wheels, server ops, or both. Revisit trigger documented: corpus > ~50k
  chunks or p95 query > 200 ms).
- `adapters/embed/` (real, HTTP via stdlib) + `adapters/mock/embed.sh`-equivalent
  (deterministic hash-based vectors so tests and demos are stable); conformance test
  covers the verb set (`embed_texts`, `dims`); unknown verb exits 64.
- No credentials → the port reports `unconfigured` and every consumer falls back to
  TF-IDF **silently and correctly** (pinned).
- Settings view + `check-integrations` cover the new credentials (secret,
  write-only).

### 3.2 Artifact vector index — **M**
**As a** QA, **I want** plans, exemplar specs, knowledge chunks and ticket texts
embedded into a queryable index with provenance, **so that** similarity is semantic
("checkout discount boundary" ≈ "cart price reduction edge case"), not just lexical.
**AC:**
- `engine/lib/vector_index.py`: `index(chunks)` embeds new/changed chunks only
  (sha256 skip — an unchanged corpus costs zero embedding calls) into
  `reports/knowledge-index/vectors.db` (SQLite, gitignored); `query(text, k,
  kind_filter)` returns `[{chunk_id, score, provenance}]`.
- Incremental: `make maintain` refreshes; `make index-rebuild` forces.
- Embedding spend is metered through the same `budget.py`/`spend` pipeline as phases
  (Epic 1 sees it), **and capped**: `budgets.max_embed_usd_per_day` in org-config —
  over the cap, indexing stops (TF-IDF fallback covers queries) and notifies. The
  cost-saving layer must never become its own runaway bill.
- Excluded from the state bundle (derived data, like `catalog.db`) — rebuilt on
  import; documented in `data-portability.md`.

### 3.3 Semantic plan reuse with human diff — **L**
**As a** QA, **I want** a sufficiently-similar prior approved plan offered as the
*starting draft* for a new ticket — shown as a diff against what a fresh authoring
produced or would produce — **so that** repeat-shaped tickets cost an edit, not a
full authoring chain.
**AC:**
- `plan_similarity.py` gains a semantic backend via `vector_index` (TF-IDF fallback
  intact); above a `reuse_threshold` (org-config, conservative default 0.80) the
  plan phase is offered in **reuse mode**: the prior plan is adapted (key/scenario
  ids re-stamped) and marked `draft` with `reused_from: <KEY>` provenance on the plan
  state.
- **Human gate unchanged and unavoidable**: reuse mode still stops at `draft`; the
  plan editor shows a "Reused from PROJ-x (similarity 0.87)" banner + the
  diff-since-reuse alongside the existing similar-plans strip. The adversary still
  runs on the reused draft (it challenges reuse staleness exactly like author
  blind-spots).
- Below threshold: today's behavior, byte for byte. `AIQE_PLAN_REUSE=0` kills it.
- The ticket comment and trace matrix carry the `reused_from` provenance.
- Pinned: a reused plan can never land as `approved`; empty-corpus and
  no-embeddings estates never enter reuse mode.

### 3.4 Exemplar retrieval for generation — **M**
**As a** Dev, **I want** `spec_exemplars.py` to pick exemplars by semantic relevance
to the change (diff/plan scenarios) rather than only repo-level heuristics, **so
that** the generate phase sees the *most transferable* existing spec, improving
first-pass quality (fewer repair loops = fewer tokens).
**AC:**
- `spec_exemplars.py` consults `vector_index.query(scenario_text, kind=exemplar,
  repo=target)`; deterministic penalties (legacy/-path) still apply after ranking;
  heuristic order is the no-embeddings fallback.
- Repair-loop count per run (already in validate contracts) becomes the tracked
  before/after metric on the eval benchmark.

### 3.5 Testdata and scenario snippet reuse — **M**
**As a** QA, **I want** generated testdata sets and recurring scenario shapes
(boundary/authz/negative patterns per domain) indexed and offered to the testdata
and testplan phases as retrieved context, **so that** the platform stops re-deriving
the same discount-boundary table for the fifth checkout ticket.
**AC:**
- `testdata/<KEY>/` and approved plans' scenario blocks are chunked (2.1 `kind:
  testdata|scenario`) and indexed (3.2).
- The testdata phase context includes top-k retrieved sets labelled as DATA/examples;
  the contract must still emit its own files (no blind copy) — the phase cache
  already de-duplicates the identical-input case.
- Pinned: retrieved snippets appear in context assembly's audit header.

---

## Epic 4 — Provider-side prompt caching

Cache-ordered assembly (already shipped) made prefixes cacheable; this epic actually
**claims the discount** and proves it.

### 4.1 Cache breakpoints on real phase calls — **M**
**As an** EM, **I want** the stable prefix (system/prompt template + estate context)
marked with provider cache-control on real `claude -p` calls, **so that** repeat
phases within the TTL pay cache-read prices (~10%) for the dominant payload.
**AC:**
- `run_phase.sh` (real path) passes the provider caching flags supported by the
  installed CLI for the template + shared-context block; RUN PARAMETERS stays after
  the final breakpoint. Where the CLI offers no explicit flag, verify and document
  the CLI's implicit caching behavior instead — no cargo-cult flags.
- `spend` records (1.1) capture `cache_read_tokens`, so the discount is *visible*.
- OpenHands launches: `agent_context.py`'s ordering contract documented as the
  cache-alignment guarantee for ACP conversations (already pinned; extend the pin to
  forbid any sub-block reordering regression).

### 4.2 Prompt-cache hit-rate report — **S**
**As an** EM, **I want** cache-read vs fresh-input token ratios per phase in the
cost report, **so that** a prefix-breaking prompt edit shows up as a falling hit
rate (complements 1.4's cost alarm).
**AC:** `cost-report` prints hit-rate per phase over the window; below-floor rates
flagged; documented in cost-optimization.md.

---

## Epic 5 — Spend controls the whole feature set respects

### 5.1 No-op phase skipping — **S**
**As an** EM, **I want** phases that cannot change the outcome skipped
deterministically (critic with zero generated tests, adversary on a zero-scenario
plan, testdata when the plan declares no data needs), **so that** free savings stop
being left on the table (cost-optimization §3 item 3).
**AC:** skip decisions logged in the run record as `{phase, skipped: reason}`; the
wizard/report render them as skipped-not-failed; pinned per phase.

### 5.2 Per-workflow budget envelopes — **M**
**As a** Lead, **I want** budget ceilings per workflow and per key-class (PR runs
cheaper than JIRA plan+generate), with the existing exit-77 semantics, **so that**
one runaway ticket cannot eat the month.
**AC:** `budgets:` gains per-mode envelopes; `budget.py` picks the envelope from the
trigger; over-envelope aborts pre-phase with the existing notify path; queue intake
warns when a key's *history* already exceeds its envelope.

### 5.3 Degradation ladder instead of hard stop — **M**
**As a** Dev, **I want** a run near its budget to degrade deliberately (drop to the
cheaper tier for remaining non-judgement phases → shrink context budget → then
abort), **so that** I get a usable, honestly-labelled result more often than an
exit 77.
**AC:** ladder steps recorded in the run record and surfaced in the wizard
("completed in reduced-cost mode"); judgement phases (`testplan`, adversary,
`generate`) never silently downgrade — they abort instead; each rung pinned.

---

## Epic 6 — Dashboard and UX for cost

### 6.1 Cost view — **M**
**As an** EM, **I want** a dashboard Cost view: spend over time, per-workflow split,
top-10 expensive keys, phase-cache + prompt-cache savings, embedding spend,
turn-calibration table, **so that** the whole story lives where the team already
works.
**AC:** new `data-go="cost"` view fed by `/api/cost-report`; measured vs simulated
clearly labelled; zero-spend mock estates show an explanatory empty state, not zeros
pretending to be data.

### 6.2 Reuse provenance surfaced everywhere — **S**
**As a** Lead, **I want** every reused artifact visibly labelled (plan editor
banner, ticket comment line, trace-matrix column, run record), **so that** reuse
never masquerades as fresh authorship in an audit.
**AC:** `reused_from` renders in all four places; absent for fresh artifacts;
pinned in the trace-matrix test.

### 6.3 Savings counterfactual on the Overview — **S**
**As an** EM, **I want** an honest "estimated avoided spend" tile (phase-cache hits
× median phase cost + cache-read discounts + skipped phases), **so that** the
platform's cost story is quantified with its methodology one click away.
**AC:** tooltip/expander states the formula and the simulated/measured caveat.

---

## Epic 7 — Testing and quality assurance for the cost stack

### 7.1 Regression pins for every mechanism — **M**
**As a** QA, **I want** each cost mechanism pinned by tests the way the phase cache
already is, **so that** a refactor cannot silently un-save the money.
**AC:** pins for: context assembly determinism + resolved-repo survival (2.2);
TF-IDF fallback correctness with no embed credentials (3.1); embed-call skip on
unchanged corpus (3.2); reuse-mode never auto-approves (3.3); prefix byte-stability
under context scoping (2.2/4.1); no-op skip matrix (5.1); degradation-ladder rungs
(5.3); spend-block schema (1.1). Suite target: all green alongside the existing
~600.

### 7.2 Reuse-quality eval benchmark — **L**
**As a** Lead, **I want** an eval extension that scores reused/scoped outputs
against fresh full-context outputs on fixture tickets, **so that** every cost lever
has a measured quality delta before it defaults ON.
**AC:** `make eval` gains paired fixtures (fresh vs reuse vs scoped-context);
scorecard reports quality delta + token delta per lever; a lever with >5% quality
regression cannot default ON (documented gate in the ADR); results feed the 2.2
rollout decision.

### 7.3 Adversarial cost-stack UAT — **M**
**As a** QA, **I want** the UAT playbook (REVIEW.md Pass 7 style) extended to the
new surfaces, **so that** the cost stack meets the same bar as the rest.
**AC:** probes for: poisoned chunk text framed as instructions (must be treated as
DATA); vector store corruption (fs_lock-style quarantine, TF-IDF fallback);
reuse-threshold boundary abuse; budget-envelope bypass attempts via queue force;
cost-report with torn run records. Findings fixed + pinned before GA.

---

## Epic 8 — Deployment and operations

### 8.1 Persistence and portability of the new stores — **S**
**As an** Op, **I want** clear persistence rules for the new artifacts, **so that**
restarts and migrations behave like the rest of the platform.
**AC:** `reports/knowledge-index/` (chunks + vectors) documented as derived data on
the PVC: excluded from the state bundle, rebuilt by `make maintain`/import
post-step; `clear-demo` removes it; `--factory` too; deployment.md updated.

### 8.2 Rollout flags and safe defaults — **S**
**As an** Op, **I want** each lever behind its own flag with a documented default
(`AIQE_CONTEXT_SCOPE`, `AIQE_PLAN_REUSE`, `AIQE_PROMPT_CACHE`, existing
`AIQE_PHASE_CACHE`), **so that** any regression is one env var from off, per
deployment, without a redeploy.
**AC:** flags read through the same config layering (properties < .env < env);
Settings view exposes them; `make config` lists them; docs table in README updated.

### 8.3 Nightly upkeep integration — **S**
**As an** Op, **I want** `make maintain` to own the recurring cost jobs (index
refresh, cost-baseline check, cache prune by size, turn-calibration snapshot), **so
that** the cost stack has zero new operational surface.
**AC:** new steps appear in maintain's step count with per-step logging; failures
degrade (notify + continue), never abort the nightly.

### 8.4 Documentation set — **S**
**As a** new team member, **I want** the cost architecture documented where the rest
lives, **so that** the system stays explainable.
**AC:** cost-optimization.md gains §5 (RAG/vector/reuse architecture + measured
results); architecture.md new section + diagrams.md flow for retrieval-scoped
context; user-guide section for the Cost view and reuse banners; CLAUDE.md command
entries; the embeddings ADR merged.

---

## Sequencing and dependency map

```
Sprint A (foundation):   1.1 → 1.2 → 1.5   |  2.1   |  8.2 flags scaffold
Sprint B (retrieval):    3.1 ADR+port → 3.2 |  2.2 → 2.3   |  4.1 → 4.2
Sprint C (reuse):        3.3 → 6.2  |  3.4, 3.5  |  5.1, 5.2 → 5.3
Sprint D (prove+ship):   7.1 → 7.2 → 7.3  |  6.1, 6.3  |  8.1, 8.3, 8.4  |  1.3, 1.4 (needs parity auth)
```

Hard dependencies: 1.1 before 1.2/1.4/4.2/6.1 (no telemetry, no reports); 2.1 before
2.2/3.2; 3.1 before 3.2/3.3/3.4/3.5; 7.2 gates default-ON for 2.2 and 3.3; 1.3 is
blocked on Claude CLI re-auth (REVIEW.md item 5) but blocks nothing except the
measured baseline.

## Expected impact (token-counted until 1.3 lands; same honesty rule as ever)

| Lever | Mechanism | Expected reduction on affected calls |
|---|---|---|
| Context scoping (2.2) | ~70% cut of the dominant static payload × every turn | Large |
| Prompt caching (4.1) | cache-read pricing (~10%) on the stable prefix within TTL | Large on repeat runs |
| Semantic plan reuse (3.3) | full authoring chain → one adapted draft + human edit | Large on repeat-shaped tickets |
| Exemplar retrieval (3.4) | fewer repair loops | Medium |
| No-op skips + turn caps (5.1, 1.5) | calls never made | Small–medium, free |
| Tiering + phase cache (shipped) | already banked | — |
