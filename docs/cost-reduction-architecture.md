# Cost-reduction backlog — implementation designs

Per-story technical designs for `docs/cost-reduction-stories.md`. Read that first;
this document says **how** each story is built: modules, schemas, algorithms, config
keys, flags and test pins. Grounded in the code as it exists at this revision — file
references are real.

Conventions inherited everywhere: stdlib-only Python (+ `pyyaml` in tooling paths),
`fs_lock.write_json_atomic`/`read_json_guarded` for every new state file, `pathlib`
paths, mkdir-locks for mutations, mock-first (every feature demoable with
`AIQE_MOCK=1`, no network), config layering `aiqe.properties < .env < env`.

---

## Epic 1 — Cost telemetry

### 1.1 Per-phase spend record

**The data already exists.** `run_phase.sh` tees the CLI result to `out/<OUT>.json`;
that JSON carries `total_cost_usd`, `num_turns` and `usage{input_tokens,
output_tokens, cache_read_input_tokens, cache_creation_input_tokens}`. `budget.py
record` already parses it for cost. The build is plumbing, not instrumentation:

1. **`budget.py`**: extend the ledger row from `phase \t cost` to
   `phase \t cost \t model \t in \t out \t cache_read \t cache_create \t turns`.
   New helper `phase_usage(json_file)` next to `phase_cost()` — same
   total-never-raise contract, zeros when fields are absent (mock output has none).
   `record` gains the model name from `$MODEL` (run_phase.sh exports it; pass as
   argv so the ledger is self-contained). Old two-column rows must still parse
   (`total` and `check` read column 2 only) — the ledger is per-run scratch, but a
   crashed run's leftover file must not break the next.
2. **`run_record.py`**: when assembling phases, join `out/cost.tsv` rows to phase
   contracts by phase name → `phases[i].spend = {model, input_tokens, ...,
   cost_usd, simulated}`. `simulated` = `AIQE_MOCK=1` or `AIQE_MOCK_PHASE_COST`
   set. Fan-out phases record under their `AIQE_PHASE_LABEL` name
   (`generate-e2e-api-tests-1`), which the ledger already sees as distinct rows.
3. **`bin/qa.py`**: `status --cost` adds a `cost` column (sum of spend, `~` prefix
   when any phase simulated); `artifacts <KEY>` prints the per-phase spend table
   after the phase list.

**Pins** (`test_cost_telemetry.py`): ledger row roundtrip with and without usage
fields; run record joins labels correctly for fan-out; simulated flag set under
mock; old-format ledger rows do not crash `total`.

### 1.2 Cost attribution

**`engine/lib/cost_report.py`** — pure aggregation, no LLM:

```python
def collect(days=None):     # -> [{run_id, key, mode, ts, phases:[spend...]}]
    # reports/runs/*.json glob, skipping reviews/queue/hooks-seen (the invariant)
def report(days=None):      # -> dict: totals, by_mode, by_key_top10, by_phase,
    #    by_model, phase_cache_savings, simulated_share
def to_markdown(rep):       # for `make cost-report` and team_report embedding
```

`phase_cache_savings` = hits per phase (from `phase_cache.stats()`) × that phase's
median measured `cost_usd` over the window; when no measured runs exist the line
prints `n/a (no measured runs yet)` — never a number derived from simulation.

Server: `GET /api/cost-report?days=N` → `report()`. Overview card: total + top key +
savings line. `team_report.py` calls `to_markdown` for a one-line summary with the
measured/simulated label.

**Pins**: aggregation over a fixture tree of run records (mixed simulated/measured);
top-10 ordering; the skip-list invariant (a reviews.json planted in the glob path
must not be summed).

### 1.3 Parity baseline / 1.4 regression alarm

`make cost-baseline` → `cost_report.snapshot_baseline()` writes
`reports/cost-baseline.json`: `{phase: {median_cost, median_in_tokens, n}}` from
**measured** runs only; refuses (exit 1, message) when none exist — a baseline of
simulations is worse than no baseline. `check_regression(threshold=0.25)` compares
the trailing window's medians per phase; over-threshold phases → one Notify-port
message naming phase, baseline, current, and the two most likely causes (prompt
edit broke the prefix; tier drift). Wired as a `maintain` step that no-ops (log
line) when no baseline file exists. **Pins**: synthetic 2× regression fires; no
baseline → silent skip; simulated runs never enter the baseline.

### 1.5a OpenHands payload metering

`openhands_agents.build_message()` returns the final string; both launch paths pass
`payload_chars=len(msg)` into `openhands_events.record_launch()`, stored on the
entry (`payload_est_tokens = chars // 4`). Render in `qa.py openhands` (one column)
and the conversations card tooltip. `cost_report` sums it as an estimated,
separately-billed line. **Pin**: launch records carry the field; webhook updates
never erase it (same never-regress rule as status).

### 1.5 Turn calibration

`cost_report.report()` includes `by_phase[phase].turns = {p50, p95, ceiling,
suggested}` where `suggested = min(ceiling, p95 + 2)`; rendered as a table by
`make cost-report`. No auto-apply — changing `org-config.yaml` stays human.

---

## Epic 2 — Retrieval-scoped context

### 2.1 Knowledge chunk store

**`engine/lib/knowledge_chunks.py`**. Sources = exactly what `gen_agents_md.py`
reads (registry, catalog JSONL, harvested contracts/routes, guidance from all four
ranked sources, exemplar specs via `spec_exemplars`):

```python
CHUNK = {"chunk_id": "<kind>:<repo>:<slug>", "kind": "repo-surface|guidance|"
         "exemplar|spec|testcase|catalog|scenario|testdata", "repo": "...",
         "source_path": "...", "text": "...", "sha256": "..."}
def rebuild():   # -> reports/knowledge-index/chunks.jsonl, deterministic order
def load():      # guarded read; [] on absence
```

Chunking is structural, not fixed-size: one chunk per repo surface section, per
guidance file, per exemplar spec, per catalog entry group, per approved-plan
scenario block (3.5), per testdata set (3.5). `chunk_id` derives from kind + repo +
a stable slug of the source — **not** from content, so an edited file keeps its id
and the vector index (3.2) sees it as changed-in-place via the sha256.

Hook: `gen_agents_md.py` calls `knowledge_chunks.rebuild()` after writing
AGENTS.md (one call site covers pipeline/onboarding/repo_admin/bootstrap, since all
of them regenerate AGENTS.md). `make maintain` adds an explicit rebuild step.
`clear-demo` removes `reports/knowledge-index/`.

With the testcase-index flag enabled, `index_checkouts.py` resolves every
registered E2E repository before the read-only chunk build. Complete pipeline
checkouts win; otherwise the repository's registered Scm adapter performs a
read-only clone into a validated derived-cache target. Repository acquisition
failure is persisted on the repo-surface chunk as `not_indexed` and cannot be
mistaken for an indexed repository containing zero tests.

**Pins**: byte-identical rebuild on unchanged inputs; ids stable under a content
edit; every chunk's `source_path` exists.

### 2.2 Per-run context assembly

**`engine/lib/context_scope.py`**:

```python
def assemble(phase, resolve_contract, signals, budget_tokens):
    # signals: {endpoints, routes, domains, scenario_texts} — from the diff
    #   (extend_scout._norm reused), the ticket, or the plan, per workflow
    # 1) MUST-KEEP: every resolved repo's repo-surface + contract chunks,
    #    the target repo's exemplars (generate), issue-type guidance (jira)
    # 2) DETERMINISTIC MATCHES: chunks whose text ∩ signals (normalised) — catalog
    #    entries touching the diff's endpoints, guidance naming the domain
    # 3) SEMANTIC FILL: vector_index.query(signal_text) while budget remains
    #    (skipped silently when the index is unconfigured)
    # -> (context_markdown, manifest)  — manifest lists kept + dropped chunk ids
```

Output written to `out/context-<phase>.md`; the manifest is its HTML-comment
header (`<!-- chunks: kept=[...] dropped=[...] budget=N used=M -->`) so the audit
travels with the artifact into transcripts. Ordering inside the file:
kind-then-repo-then-chunk_id — deterministic, most-stable-first (repo surfaces
before per-run matches), preserving prefix cacheability.

Pipeline integration: a thin wrapper in `pipeline.sh` builds the scoped file per
authoring phase and substitutes it for `AGENTS.md` in that phase's context list
when `AIQE_CONTEXT_SCOPE=1` (default per-phase from org-config
`context_scope: {triage: on, testplan: off, ...}` — the 2.3-gated rollout).
`budget_tokens` from `context_budget:` (default 4000, chars//4 estimator).

**Pins**: resolved-repo survival at budget 1 token (must-keep overrides budget);
two assemblies with identical inputs are byte-identical; manifest lists every
dropped candidate; `AIQE_CONTEXT_SCOPE=0` leaves today's context list untouched
(golden: the mock pipeline's phase context is unchanged byte-for-byte).

### 2.3 Retrieval quality guardrail

Eval fixtures gain `expected_context: [substrings]`; the benchmark asserts scoped
assembly retains them. `missing_context` field added to the phase contract schemas
(optional, array of strings); `pipeline.sh` on seeing it non-empty re-runs that
phase once with full context (flag `AIQE_CONTEXT_RETRY=1` default) and records
`{phase, context_retry: true, missing: [...]}` in the run record. The critic's
`new-approach` rate per benchmark fixture is captured into the scorecard for the
scoped-vs-full comparison table.

---

## Epic 3 — Vector index and semantic reuse

### 3.1 Embedding port

**ADR summary** (full text goes to `docs/adr/embeddings.md` when built):

- **Store: SQLite, vectors as float32 BLOBs, pure-Python cosine.** Corpus is small
  (hundreds to low thousands of chunks); brute-force cosine at 1k × 1024-dim is
  ~10 ms in pure Python with `struct`/`math` — no numpy, no native extension
  (sqlite-vec/FAISS rejected: native wheels on Windows/Git-Bash CI; Chroma/Qdrant
  rejected: a server dependency for a PoC that must run from `make serve`).
  Documented revisit trigger: corpus > 50k chunks or p95 query > 200 ms.
- **Embeddings: HTTP via stdlib `urllib`** against an OpenAI-compatible
  `/v1/embeddings` endpoint (`EMBED_URL`, `EMBED_API_KEY`, `EMBED_MODEL`,
  `EMBED_DIMS`) — covers Voyage, OpenAI, Azure, local TEI/Ollama without any SDK.
- **Port shape**: `adapters/embed/http.sh` speaking verbs `embed_texts`
  (JSONL stdin → JSONL vectors stdout) and `dims`; `adapters/mock/embed.sh`
  returns deterministic vectors — `sha256(text)` bytes expanded to `EMBED_DIMS`
  floats in [-1,1] — so similarity is stable across runs and platforms; unknown
  verb exits 64; both covered by `make conformance`.
- The engine calls the port only through `engine/lib/embeddings.py` (`embed(texts)
  -> [vec]`, `configured() -> bool`), which shells the adapter exactly like the
  pipeline shells SCM/Tracker. `configured()` false → every consumer takes the
  TF-IDF path.

Settings SPEC gains the four keys (`EMBED_API_KEY` secret); `integration_check`
gains a read-only probe (embed one short string, report dims).

### 3.2 Vector index

**`engine/lib/vector_index.py`**, store `reports/knowledge-index/vectors.db`:

```sql
CREATE TABLE vectors (chunk_id TEXT PRIMARY KEY, sha256 TEXT, kind TEXT,
                      repo TEXT, dims INT, vec BLOB, updated REAL);
```

```python
def refresh():   # load chunks.jsonl; embed only rows whose sha256 changed;
                 # delete rows whose chunk_id vanished; respects the daily cap
def query(text, k=5, kind=None, repo=None):  # embed text (1 call), brute cosine
def stats():     # rows, dims, last refresh, embed calls avoided (sha-skip count)
```

Daily cap: `budgets.max_embed_usd_per_day`; spend estimated from token counts ×
a per-1k price in org-config (`embed_price_per_mtok`), recorded to a
`reports/knowledge-index/embed-spend.json` day ledger; over cap → stop, notify
once, `refresh` reports partial. `make index-rebuild` truncates and refreshes.
Corruption: any sqlite error → move the db aside (`.corrupt-<ts>` like fs_lock)
and rebuild from chunks — the index is derived data, so recovery is always
regeneration, never repair.

**Pins**: sha-skip (unchanged corpus → zero embed calls, via a counting fake
adapter); vanished chunk rows deleted; corrupt db quarantined + rebuilt; cap stops
indexing but never breaks `query` fallback.

### 3.3 Semantic plan reuse

Flow (Workflow B, `pipeline.sh plan`):

```
resolve → [reuse check] → testplan (fresh | reuse-adapted) → adversary → draft
```

`plan_similarity.best_reusable(key, ticket_text)`:
semantic score from `vector_index.query(ticket_text, kind=scenario)` grouped by
source plan (fallback: existing TF-IDF `similar()`), filtered to plans whose state
is/was `approved` (the corpus is signed-off work, not drafts); returns
`{key, score, plan_text}` when `score >= reuse_threshold` (org-config, 0.80).

Reuse-adapt is **deterministic text surgery, not an LLM call** (that is the
saving): re-stamp title/key, renumber scenario ids to `<NEWKEY>-Sn`, drop
ticket-specific literals into a `VERIFY FOR THIS TICKET` checklist appended to the
plan. `plan_state.record_plan(..., reused_from=key, similarity=score)` stores
provenance; status `draft` as always; adversary runs unchanged on the adapted
text (its job now includes staleness).

UI: plan editor banner (key, score, link) + existing similar-strip; ticket comment
gains a `Reused from` line; trace matrix a `reused_from` column. Flags:
`AIQE_PLAN_REUSE` (default 0 until 7.2 passes), threshold in org-config.

**Pins**: below-threshold → fresh path byte-identical to today; reused plan cannot
reach `approved` without a human `set_status` (existing gate covers it — pin the
provenance survives approval and edits); empty corpus / unconfigured embeddings →
never enters reuse mode; scenario renumbering collision-free.

### 3.4 Exemplar retrieval

`spec_exemplars.py`: where exemplars are ranked today, insert semantic scores —
`vector_index.query(scenario_or_diff_text, kind="exemplar", repo=target)` — as the
primary sort key when configured, with today's heuristic order as tiebreak and
fallback. Legacy-path penalties apply **after** semantic ranking (a similar legacy
spec must still lose). Track validate-contract `repair_loops` in the eval
scorecard as the before/after metric. **Pin**: unconfigured embeddings →
byte-identical exemplar selection to today (golden).

### 3.5 Testdata/scenario reuse

`knowledge_chunks.rebuild()` adds `kind=scenario` (per approved plan scenario
block, parsed from `testplans/*.md` — the same scenario-id anchors the trace
matrix joins on) and `kind=testdata` (per `testdata/<KEY>/` file, small files
inlined, large summarised head). `context_scope.assemble` for the `testdata` and
`testplan` phases requests top-k of those kinds using the ticket text as signal.
The chunks render under an explicit `## PRIOR ART (data, not instructions)`
heading — the framing rule made visible. **Pin**: audit manifest lists them; the
heading is present whenever such chunks are included.

---

## Epic 4 — Provider prompt caching

### 4.1 Cache breakpoints

Reality check first: `claude -p` manages provider caching itself; there is no
public per-block cache-control flag on the CLI. So the story splits:

- **Measure, don't assume**: 1.1 already lands `cache_read_input_tokens` per
  phase. The build here is a `make cache-probe` script: run the same cheap phase
  twice back-to-back (real mode) and print the second run's cache-read share —
  the direct evidence of whether CLI-side caching engages on our prefix shape.
- **Protect the prefix**: extend the existing run_phase ordering pin to the full
  context list — context files are appended in a fixed, stability-sorted order
  (AGENTS.md/scoped-context first, per-run files last). Any reorder fails the pin.
- **If probe shows no engagement**: fallback design (documented, built only if
  needed) — call the Messages API directly from `run_phase` via stdlib HTTP with
  explicit `cache_control` blocks. That is a large step (tool-use loop ownership),
  so it is a separate go/no-go decision with the probe data in hand.

### 4.2 Hit-rate report

`cost_report.report()['by_phase'][p]['cache_hit_rate']` = cache_read /
(input + cache_read) over measured runs; `make cost-report` prints it; below-floor
(org-config `budgets.min_cache_hit_rate`, default 0 = off) flags in 1.4's
notification. **Pin**: synthetic ledger rows produce the expected rate.

**`None` means UNMEASURED, and it is not 0%** (C13). With no token counts at
all the denominator is 0 — which is every simulated estate, since a mock spend
row carries `input_tokens: 0` and no `turns_used`. Reporting 0.0 there put a
phase nobody measured beside a phase whose prefix genuinely stopped being
cached, the one distinction this column exists to make, and the floor then
flagged all of them `(BELOW FLOOR)` while naming a fix (a prefix-breaking
prompt edit, a model-tier change) for a phase where no token was ever counted.
Both renderers print `n/a`, the floor is not applied, and the table carries a
note naming `make cache-probe` — which already gives the same answer from the
other side, refusing mock mode with exit 2, "Nothing was measured". A genuine
0% (input tokens observed, none of them cache reads) is still 0% and still
flags. `turns_p50/p95` follow the same rule. **Pins**:
`test_unmeasured_cache_rate.py`.

---

## Epic 5 — Spend controls

### 5.1 No-op skips

In `pipeline.sh`, before each candidate phase: critic skipped when the merged
generate contract has zero tests; adversary skipped when the plan contract has
zero scenarios; testdata skipped when the plan declares `data_needs: none`
(schema gains the optional field). Each skip: `echo` line + a
`{"skipped": "<reason>"}` stub contract so `run_record` and the wizard render
"skipped (nothing to do)" — distinct from failure. **Pins**: each skip fires on
its fixture; a skip stub never counts as a failed phase.

### 5.2 Budget envelopes

`org-config.yaml`:

```yaml
budgets:
  envelopes: {pr: 1.50, jira: 4.00, plan: 1.00, tests: 3.00}
  review_uplift_usd: {pr: 0.75, jira: 0.75, tests: 0.75}
```

`budget.py check` resolves the envelope from `AIQE_RUN_MODE` (pipeline exports
it); explicit `MAX_COST_USD_PER_RUN` still wins (layering rule). Queue intake
(`work_queue.add`) warns (not refuses) when `cost_report` shows the key's history
already over its envelope — the warning lands in the item and the wizard shows
it. **Pins**: envelope resolution precedence; warning path.

PRD v2 B5 keeps those four values as the base envelope. When the generated-test
reviewer actually runs, `budget.workflow_envelope` adds the configured
provisional $0.75 planning allowance to PR/JIRA/tests and exposes the same
effective cap to queue intake. Disabled/off review and plan-only work add zero;
an explicit `MAX_COST_USD_PER_RUN` still wins. The allowance is not a measured
cost claim and must be recalibrated after real reviewer traffic.

### 5.3 Degradation ladder

`budget.py check` gains graded results: `ok | degrade_tier | degrade_context |
abort` at 60/80/100% of the envelope. `run_phase.sh` consults it: `degrade_tier`
maps non-judgement phases (`triage,analyze,testdata,critic,validate`) to the
haiku tier if not already there; `degrade_context` halves `context_budget` for
scoped phases. Judgement phases, including `reviewer` and `reviewrepair`,
ignore degradation and abort at 100% as today.
Every rung: run-record entry + wizard chip ("reduced-cost mode"). **Pins**: rung
thresholds; judgement phases never downgrade; run record carries the ladder.

---

## Epic 6 — Dashboard

6.1: new `data-go="cost"` view — four cards (trend by day, per-mode split, top
keys table, savings + hit-rate + turns calibration) all from one
`/api/cost-report` payload; measured/simulated badge on every number
(`simulated_share` drives it). 6.2: `reused_from` renders in plan editor banner
(plans/one), ticket comment (plan_state.ticket_comment), trace matrix column
(trace_matrix.FIELDS + build), run record (already via plan state). 6.3:
Overview tile = `phase_cache_savings + prompt_cache_savings + skipped_phase
count × median`, tooltip carries the formula text verbatim from cost_report.

---

## Epic 7 — Test plan

New suites: `test_cost_telemetry.py`, `test_knowledge_chunks.py`,
`test_context_scope.py`, `test_vector_index.py` (fake counting adapter),
`test_plan_reuse.py`, `test_spend_controls.py` — each pinning the ACs called out
above. Eval: `eval/benchmark` gains paired fixtures with `expected_context`;
scorecard gains `quality_delta` and `token_delta` per lever; the >5% gate is a
scorecard assertion, so `make eval` itself fails when a lever regresses quality
— the go/no-go is mechanical. UAT probes (7.3) follow the Pass-7 playbook and get
run before any default flips ON.

## Epic 8 — Ops

8.1: `reports/knowledge-index/` documented in deployment.md + data-portability.md
as derived (bundle-excluded; `EXCLUDE_PARTS` gains `knowledge-index/`); import's
"Next:" line gains `make index-rebuild`. 8.2: flags
`AIQE_CONTEXT_SCOPE/AIQE_PLAN_REUSE/AIQE_CONTEXT_RETRY` + existing
`AIQE_PHASE_CACHE` in settings SPEC + `make config`. 8.3: maintain adds four
steps (chunks rebuild, index refresh, baseline check, index prune) with the
notify-and-continue failure rule. 8.4: docs listed in the story.

---

## Build order (maps to the stories' sprint plan) — ALL SLICES SHIPPED

| # | Slice | Stories | New/changed files |
|---|---|---|---|
| 1 | Telemetry | 1.1, 1.2, 1.5, 1.5a | budget.py, run_record.py, cost_report.py*, qa.py, dashboard_server.py, Makefile |
| 2 | Chunks | 2.1 | knowledge_chunks.py*, gen_agents_md.py, demo_data.py |
| 3 | Embed port + index | 3.1, 3.2 | embeddings.py*, adapters/embed/*, adapters/mock/, vector_index.py*, settings SPEC, integration_check.py, conformance |
| 4 | Context scoping | 2.2, 2.3 | context_scope.py*, pipeline.sh, contracts schemas, eval fixtures |
| 5 | Reuse | 3.3, 3.4, 3.5 | plan_similarity.py, plan_state.py, spec_exemplars.py, dashboard.py, trace_matrix.py |
| 6 | Controls + caching | 5.1–5.3, 4.1, 4.2 | pipeline.sh, budget.py, run_phase.sh, cache-probe |
| 7 | UX + baseline | 6.1–6.3, 1.3, 1.4 | dashboard.py/server, maintain, cost-baseline |
| 8 | Harden + ship | 7.1–7.3, 8.1–8.4 | tests, eval, docs, deploy manifests |

`*` = new module. Each slice ends demo-green (`make review`) before the next
starts; flags keep unfinished levers OFF so main stays shippable throughout.
