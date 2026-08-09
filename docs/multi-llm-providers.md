# Multi-LLM providers — design & user stories

Let users run the pipeline's LLM phases on **different providers — local Ollama,
Claude Code (today's default), OpenAI Codex CLI, or OpenHands as an external
model provider — switched from Settings**, with per-provider **cost tracked in
the UI**. Design first; build slices at the end.

## 1. Architecture review — what makes this tractable

Three facts about the current code decide the whole design:

1. **One choke point.** Every real LLM call goes through
   `engine/phases/run_phase.sh` (the `claude -p` wrapper) — phase policy
   (model, max_turns, allowed_tools) from org-config, prompt assembled
   cache-ordered, result JSON teed to `out/<phase>.json`, contract extracted
   and schema-checked after. Providers swap **under** this wrapper; nothing
   upstream changes.
2. **The port precedent exists.** The Embedding port (ADR-9) already proved
   the shape: `adapters/<port>/` + a deterministic mock + conformance verbs +
   engine-side client module + Settings section + check-integrations probe.
   The LLM Runner becomes the **eighth port** with the same discipline.
3. **Most phases don't actually need an agentic runtime.** The wrapper
   pre-concatenates every context file into the prompt, so read-only phases
   (`triage`*, `analyze`, `planadversary`, `critic`) need only
   a **chat completion**. And SDD made the plan-family artifacts DERIVED:
   `testplans/<KEY>.md` is rendered from the contract, testdata files are
   listed in the contract — so `testplan`/`planarbiter`/`testdata` can run
   completion-style with **harness-side materialization**. Only
   `generate`/`validate` genuinely require an agentic loop (multi-file edits
   in the test repo + executing tests in repair loops).
   *`triage` lists `Bash(git diff:*)` but the diff is already passed as
   context (`out/pr.diff`) — completion-capable in practice.

### Capability classes (the honest matrix)

| Class | Phases | Needs | Ollama | Claude Code | Codex CLI | OpenHands |
|---|---|---|---|---|---|---|
| completion | triage, analyze, planadversary, critic | one chat completion over pre-injected context | ✅ | ✅ | ✅ | ✅ |
| completion + derived writes | testplan, planarbiter, testdata | completion + harness materializes files from the contract | ✅ | ✅ | ✅ | ✅ |
| agentic | generate, validate, reviewrepair | tool loop: Read/Write/Edit/Bash **in our workspace**, bounded turns | ❌ | ✅ | ✅ | ❌ (see below) |

> **Correction (slice 5).** OpenHands was originally marked ✅ for agentic
> "(delegated conversation)". It is not. The agent runs in **its own sandbox**,
> so files it writes never land in `workspace/tests/<repo>` where the gate
> looks. Closing that gap needs either the agent pushing its own branch —
> which the constitution forbids, the gate is the only push path — or a
> fetch-back channel. So OpenHands serves the **completion** class: we harvest
> its final message as the contract and the harness materializes artifacts,
> exactly as for a local model. Having OpenHands *author tests* remains fully
> supported the way it always was — as a **trigger** that runs the pipeline,
> where the gate still commits.

A **capability check at config time** refuses an impossible assignment
("ollama cannot run `generate` — agentic phases need claude or codex")
instead of failing mid-run.

**Agentic is not uniform** (slice 4 finding). Codex runs a real tool loop, but
two guarantees the claude adapter provides do not survive the port, and the
adapter says so rather than pretending:

| Guarantee | claude | codex |
|---|---|---|
| per-tool allow-list | `--allowedTools` exactly | mapped onto a **sandbox**: `Write`/`Edit` in the policy → `workspace-write`, otherwise `read-only` |
| turn ceiling (`phases.*.max_turns`) | enforced by the CLI | **not enforced** — no equivalent flag; the result JSON reports `turn_limit_enforced:false` and the budget ceiling (exit 77) is the backstop |
| cost | provider-reported USD (`reported`) | tokens only → priced from `pricing:` (`estimated`, `~$`) |

The sandbox mapping is coarser but preserves the property that matters: an
opinion-only phase (critic, plan adversary) still cannot write, so "advisory"
keeps meaning advisory.

**Model ids are configured, never guessed.** `llm_runner.check_model_mapping`
refuses a provider that would receive a claude-namespace id, naming the exact
`llm.models_by_provider.<provider>` key to set — that failure otherwise
surfaces as an "unknown model" error from the vendor CLI, several layers below
the provider switch that caused it. Codex ships pre-mapped so a bare switch
works; Ollama deliberately does not, because the right id depends on which
models the operator has pulled.

## 2. The LLM Runner port (design)

```
adapters/llm/claude.sh     today's claude -p invocation, extracted verbatim
adapters/llm/ollama.sh     OpenAI-compatible /v1/chat/completions (stdlib HTTP,
                           no SDK — same rule as the Embed adapter); local =
                           no credentials, OLLAMA_URL + per-tier model names
adapters/llm/codex.sh      `codex exec` headless (agentic, JSON output);
                           CODEX_BIN (auth is the CLI's own, as with claude);
                           tokens harvested from its JSONL event stream
adapters/llm/openhands.sh  delegates the phase as a conversation via the
                           existing openhands_client rails (launch record
                           reused; `events`/`final_message` added as the PULL
                           path); polls to a timeout, harvests the final agent
                           message. COMPLETION class (its sandbox is not our
                           workspace) and opt-in via AIQE_OPENHANDS_PROVIDER=1:
                           highest latency, unmeterable spend
adapters/mock/llm.sh       thin shim over mock_phase.sh (existing behavior)
```

**Verbs** (conformance-tested, unknown verb exit 64):
- `run_phase <phase> <prompt_file> <workdir> <out_json> [context...]` —
  execute; write the provider-normalized result JSON to `<out_json>`
- `capabilities` — prints `completion` or `agentic`
- `check` — read-only reachability probe (for check-integrations)

**Normalized result JSON** (what every adapter must produce — the contract
that keeps telemetry provider-agnostic):

```json
{"result": "<final text incl. the JSON contract>",
 "usage": {"input_tokens": N, "output_tokens": N,
           "cache_read_input_tokens": N},
 "num_turns": N, "total_cost_usd": 0.0123,   // omitted when unknown
 "provider": "ollama", "model": "qwen2.5-coder:14b"}
```

`run_phase.sh` becomes a dispatcher: resolve the phase's runner (config
below) → capability check → invoke the adapter → the EXISTING tail
(contract extraction, schema validation, budget record, phase cache, context
retry) runs unchanged. The phase cache key already includes the model — it
gains the provider, so switching providers can never replay another
provider's cached result.

**Derived-writes materialization**: for the completion+derived class on a
completion runner, the wrapper materializes artifacts AFTER contract
extraction — testplan/planarbiter via the SDD renderer (already the source of
truth), testdata from the contract's `fixtures[]` (content embedded in the
contract by a prompt addendum those providers get). Agentic runners keep
writing files themselves, exactly as today.

### Configuration & the Settings switch

```yaml
# org-config.yaml
llm:
  provider: claude              # global default: claude|ollama|codex|openhands|mock
  phase_providers: {}           # optional per-phase override, e.g.
                                #   triage: ollama, analyze: ollama
  models_by_provider:           # tier names stay per-phase in `models:`; each
    ollama:                     # provider maps the claude ids to its own
      claude-haiku-4-5-20251001: qwen2.5-coder:14b
      claude-sonnet-4-6: qwen2.5-coder:32b
pricing:                        # $/Mtok for providers that report tokens only
  codex: {gpt-5-codex: {in: 1.25, out: 10.0}}
  ollama: local                 # tokens tracked, cost rendered "$0 (local)"
```

Settings gains an **"LLM provider"** section: provider select (writes
`AIQE_LLM_PROVIDER` — env layering as everywhere, so the UI switch takes
effect on the next run without a restart), `OLLAMA_URL`, `OLLAMA_API_KEY`
(secret) and `CODEX_BIN`. *(As built: codex authenticates through its own CLI
exactly like claude, so there is no `CODEX_API_KEY` for us to hold — the only
setting worth having is where the binary lives.)* `check-integrations` probes
the ACTIVE provider (and any per-phase ones).

**No silent fallback** (guardrail): an unreachable provider fails the phase
with `PROVIDER_UNREACHABLE` / `PROVIDER_UNAVAILABLE` + the fix — the platform never
silently reroutes to a different (possibly paid) provider. Mock mode is
untouched: `AIQE_MOCK=1` short-circuits before provider selection, so demos
and the suite never depend on any provider being installed.

### What deliberately does not change

The gate (providers generate; only the gate commits), tool policy (agentic
adapters receive the same `allowed_tools` and must enforce it as closely as
their runtime allows — claude exactly, codex via the sandbox mapping above;
the invariant an adapter may never break is that a read-only phase stays
unable to write), prompts (provider-agnostic; the derived-writes addendum
is appended by the wrapper, not forked per provider), the mock posture, and
the budget guard (which meters whatever the normalized JSON reports).

## 3. Cost tracking from the UI (mostly extends what ships)

The telemetry (cost-reduction 1.x) already records per-phase
model/tokens/turns/cost and renders the Cost view. Provider support adds:

- **Provider column in the ledger + spend blocks** (`provider` from the
  normalized JSON) → `cost_report` gains `by_provider` rollup; the Cost view
  gains a per-provider card and a provider badge on the phase table.
- **Price-table costing**: when the adapter reports tokens but no
  `total_cost_usd` (codex, openhands-delegated), `budget.py` computes cost
  from `pricing:` — labelled `≈` (list-price estimate) to keep the iron rule:
  a computed figure never masquerades as a provider-reported one, and
  simulated never masquerades as either.
- **Local honesty**: Ollama runs render **"$0 (local)"** with tokens still
  tracked — visibly free, never invisibly uncounted. A "local vs cloud
  tokens" split lands on the Cost view so an EM sees exactly what moving
  triage/analyze to Ollama avoided.
- Budgets/envelopes/degradation ladder apply unchanged (local $0 spend simply
  never trips them).

## 4. User stories

**E1 — the port (no behavior change)**
- **1.1 (M)** As an Op, I want the claude invocation extracted into
  `adapters/llm/claude.sh` behind the runner dispatch, so that today's
  behavior is byte-identical (pinned: mock demo + parity path untouched)
  while the seam exists. AC: normalized-JSON contract; conformance verbs;
  phase-cache key gains provider.
- **1.2 (S)** As an Op, I want capability declarations + config-time
  validation, so that an impossible phase→provider assignment is refused
  with the fix named, never discovered mid-run.

**E2 — providers**
- **2.1 (M)** Ollama adapter (completion class): stdlib HTTP, model map per
  tier, works fully offline; completion phases produce valid contracts on a
  local model. AC: `PROVIDER_UNREACHABLE` on a down daemon; no silent
  fallback (pinned).
- **2.2 (M)** Derived-writes materialization for testplan/planarbiter/
  testdata on completion runners (SDD renderer + contract fixtures); pinned
  against the agentic path producing identical artifacts on the mock estate.
- **2.3 (M)** Codex adapter (agentic): `codex exec` headless, tool policy
  passed through, usage harvested, price-table costing (`≈`).
- **2.4 (L)** OpenHands-as-provider (experimental): phase delegated as a
  conversation via the existing client (launch/request/payload records
  reused); poll-with-timeout for the contract; failure = the phase fails
  actionably. Flagged experimental in Settings.
  *As built:* **completion class only** (see the correction above), opt-in via
  `AIQE_OPENHANDS_PROVIDER=1` rather than a dropdown — a phase becomes a
  conversation (minutes, not seconds) and its spend lands on an account this
  platform cannot meter. The client gained a PULL path (`events`,
  `final_message`) because the webhook only arrives if OpenHands can reach a
  receiver we own, and a phase cannot wait on a callback that may never come.
  The launch is recorded **before** the poll loop, so a conversation the user
  is paying for stays reachable even if we die waiting. Cost basis is
  `unknown` — not 0.
  **Optionality:** `AIQE_OPENHANDS=off|auto|required` governs the optional
  *trigger* path, where an outage is `degraded`. Selecting OpenHands as the
  LLM provider makes it load-bearing for that run **by construction**, so
  there an outage is a failed phase. That does not weaken the standalone
  guarantee: no trigger path depends on it, and `engine/` still never imports
  the client — the adapter does, which is exactly what the port boundary is
  for.
- **2.5 (S)** Per-provider parity harness: `make parity-pr
  LLM_PROVIDER=ollama` etc. — the same four quality claims measured per
  provider before anyone trusts a cheap model with judgement phases.
  *As built:* plus `make parity-compare`, which puts providers side by side on
  commit rate / critic score / $ per run / turns from the run records, groups a
  multi-provider run as `mixed:a+b` rather than attributing it to either, and
  EXCLUDES simulated runs (counted separately) — averaging a mock run in would
  report a provider as cheap when nothing was measured. Blocked on the same
  real-LLM auth as `make parity-*`, so it currently prints "No MEASURED parity
  runs yet" rather than a comforting table.

**E3 — Settings switch + routing**
- **3.1 (M)** Settings "LLM provider" section (select + per-provider fields,
  secrets write-only) writing env via the existing layering; per-phase
  `phase_providers` in org-config for mixed estates (local triage, claude
  generate). AC: switch effective next run, no restart; both example files
  covered (the coverage pins enforce).
- **3.2 (S)** `check-integrations` probes the active + per-phase providers
  read-only, with fix hints.

**E4 — cost in the UI**
- **4.1 (M)** Provider-aware telemetry end to end: ledger/spend `provider`
  field, `by_provider` rollup, Cost-view provider card + badges,
  `qa.py status --cost` provider column. Labels: provider-reported ($),
  price-table (≈$), local ($0 (local)), simulated (~). AC: the four label
  classes are pinned and can never cross.
- **4.2 (S)** Local-vs-cloud split + "avoided cloud tokens" line on the Cost
  view — the number that justifies (or kills) the local-model experiment.
  Shipped with 4.1: `local_tokens`/`cloud_tokens` on the report, rendered by
  `#cost-localsplit` (hidden when there are no tokens to split, so a fresh
  estate shows nothing rather than a meaningless `0 vs 0`).

**E5 — safety & conformance**
- **5.1 (S)** Conformance: all LLM adapters pass verb/unknown-verb checks;
  every adapter must echo its effective tool policy via a `tool_policy` verb,
  and conformance asserts the answer is never MORE permissive than the policy
  it was given. *As built:* the check bites — pointing codex's read-only branch
  at `workspace-write` fails conformance with the reason.
- **5.2 (S)** Constitution clause: "No silent provider fallback; provider
  switches are explicit configuration" + its pin.
- **5.3 (M)** Adversarial UAT (Pass-10 style): provider outage mid-run,
  malformed provider JSON, price-table absence (cost renders unknown — never
  0), a completion provider assigned an agentic phase, cache poisoning
  across providers. *As built:* `make test-providers`
  (`tests/provider-adversarial.sh`), 8 attacks (the R1 unpriced-provider and R3a
  cache-key attacks were added by the multi-pass review), wired into `make review`.

## 5. Build order

| Slice | Stories | Gate | Status |
|---|---|---|---|
| 1 | 1.1, 1.2 | suite green, demo byte-identical | shipped |
| 2 | 2.1, 2.2, 3.2 | completion phases green on a local Ollama | shipped |
| 3 | 3.1, 4.1, 4.2 | UI switch + provider-labelled Cost view | shipped |
| 4 | 2.3 | codex parity on the demo estate | shipped |
| 5 | 2.4 | openhands-delegated phase, experimental flag | shipped |
| 6 | 2.5, 5.1–5.3 | per-provider parity + UAT before any default change | shipped |

Default provider stays **claude** throughout; judgement phases
(testplan/adversary/generate) stay on an agentic, proven provider until 2.5's
per-provider parity measures them — the same quality-gated rollout discipline
as context scoping and plan reuse.

---

## 6. The Batch provider (`adapters/llm/batch.sh`, slice 1 — BUILT)

Anthropic's **Message Batches API** at 50% of the synchronous price. Design and
sizing live in `docs/prd-batch-api-cost-reduction.md`; this section is what
shipped.

**It is COMPLETION class, and that is a capability fact rather than a choice we
made.** A batch request is a single Messages call. The model may return
`tool_use` blocks, but every turn needing a *client* tool result would be
another batch submission at roughly an hour each — so `generate`, `validate`
and `reviewrepair` are refused at config time by `llm_runner.check_assignment`,
naming `llm.phase_providers` as the fix. Everything else — `triage`, `analyze`,
`testplan`, `planadversary`, `planarbiter`, `testdata`, `critic`, `reviewer` —
works exactly as it does for `ollama`, with `derived_writes.py` materializing
the plan family's artifacts from the contract.

**Auth is not the CLI's.** `claude -p` commonly authenticates with a
subscription; the Batch API needs `ANTHROPIC_API_KEY`. There is no batch flag in
the Claude Code CLI at all (checked: `claude --help` on v2.1.220 has zero
matches for "batch"), so this adapter talks HTTP directly. A missing key is a
refusal that says so — never a fallback to the paid synchronous provider (C12),
because the operator enabled batch to spend *less*.

**Configuration.** Selection uses the existing mechanisms, so batch is
per-phase configurable on day one:

```yaml
llm:
  phase_providers:
    testplan: batch          # authored overnight, 50% off
    generate: claude         # agentic; must stay on the CLI
```

or `AIQE_LLM_PROVIDER=batch` for everything eligible. Waiting behaviour:
`AIQE_BATCH_MAX_WAIT_MIN` (90), `AIQE_BATCH_POLL_SECONDS` (20),
`AIQE_BATCH_MAX_TOKENS` (8192) — all in Settings and both example files.

**Outcomes are kept distinct, because three of them are easy to get wrong:**

| Outcome | Reported as |
|---|---|
| `succeeded` | the phase ran; tokens recorded, no dollar figure |
| `errored` | the request failed, with the API's error |
| `expired` / `canceled` | **not billed**, and **nothing is known** about the phase — the model never saw it. Not a refusal, not an empty answer |
| still processing at the deadline | `BATCH_STILL_PROCESSING`, **naming the batch id** — giving up waiting does NOT cancel it; it keeps running and is still billed |

**Cost is `estimated`, never `reported`.** The API returns tokens, not dollars,
so the adapter emits no `total_cost_usd` and `budget.priced()` derives the
figure from org-config `pricing:` with a `~`. `pricing.batch` ships **unset**,
so batch spend reads `unknown` — never 0 — until an operator enters their own
(already-halved) rates. An unknown cost is honest; a wrong hardcoded rate would
understate a real bill, which is the R1 defect this stack exists to prevent.

**`custom_id` is load-bearing, not decorative.** Batch results may be returned
in any order — the API's own example returns the second request before the
first — so even a one-request batch is correlated by id. The pins answer with a
decoy row first, so a positional shortcut fails immediately.

Pins: `registry/tests/test_batch_provider.py` (14), most of them driving the
adapter end to end against a local stub of the API rather than grepping source.
Mutation: 9 mutations, 9 killed.
