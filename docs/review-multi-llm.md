# Multi-pass review — LLM Runner port and the cost stack

Scope: the six multi-LLM slices (`adapters/llm/*`, `engine/lib/llm_runner.py`,
`derived_writes.py`, `parity_compare.py`, `openhands_client.py`) plus the code
they changed the meaning of (`budget.py`, `phase_cache.py`, `cost_report.py`,
`run_record.py`, `engine/phases/run_phase.sh`).

Four passes: **architecture/boundaries → line-level correctness → functional
edge cases → adversarial verification**. Every finding below carries a
reproduction that was actually run; findings that did not survive verification
were dropped rather than reported as risks.

The theme is one the codebase already names as its own standard: the failures
that matter are the **silent** ones. Each finding here is something that
produced a confident, wrong, reassuring answer.

---

## R1 — Unpriced providers silently disable the entire spend-control stack
**Severity: critical · Status: FIXED**

`budget.record()` sets `metered` only when a cost figure exists. A provider
with no `pricing:` entry yields `cost=0.0, metered=False, basis="unknown"`.
Both spend controls then no-op, because both are gated on `metered > 0`:

* `check()` — the exit-77 ceiling — returns `None` (within budget)
* `grade()` — the 60/80/100% degradation ladder — returns `ok` forever

Reproduction (5 `generate` phases, 27.5M tokens, `MAX_COST_USD_PER_RUN=1.00`):

```
unpriced provider: total=$0.00 metered=0 unmetered=5
  budget.check() -> None
  budget.grade() -> ok
  ledger bases   -> ['unknown','unknown','unknown','unknown','unknown']
```

A run could burn an arbitrary amount and report *"$0.00, within budget"*. The
per-row honesty (`basis: unknown`, added in slice 3) was correct and had no
consumer — the aggregate threw the information away.

**Fix.** Cost cannot be invented, so the ceiling still cannot abort on unknown
spend — but the *inability to enforce* is no longer silent:

* `budget.unpriced()` → `(calls, providers)` with basis `unknown`
* `budget.enforceability()` → `enforced | partial | unenforceable` + a message
  naming the exact `pricing:` key to add
* `budget.py check` prints it to stderr after a passing check, so "within
  budget" is never printed alone about spend that could not be weighed
* `run_record` gains a `budget` block (`enforceability`, `unpriced_calls`,
  `unpriced_providers`) so no report can imply a ceiling was applied
* `cost_report` gains `unpriced_calls` / `unpriced_providers`, and the markdown
  leads with **"This total is incomplete"** when they are non-zero

Pins: `test_unpriced_provider_cannot_silently_disable_the_budget`,
`test_cost_report_never_prints_a_total_that_hides_unpriced_spend`.

---

## R2 — A hardcoded provider name overrode explicit pricing configuration
**Severity: high · Status: FIXED**

```python
if table == "local" or provider == "ollama":   # <- the second clause
    return 0.0, "local"
```

`adapters/llm/ollama.sh` is a plain OpenAI-compatible HTTP client — its own
header says it also serves LM Studio, vLLM, llama.cpp "and any local gateway".
Nothing stops `OLLAMA_URL` pointing at a **paid hosted** endpoint. An operator
who priced it in org-config still got `$0 (local)`:

```
explicit price table for ollama -> (0.0, 'local')
expected                        -> (45.0, 'estimated')
```

That is the exact understatement `priced()` exists to prevent, and it beat the
operator's own configuration to produce it.

**Fix.** Configuration wins; the hardcoded name is gone. `org-config` still
ships `pricing: {ollama: local}`, so a genuinely local daemon is unchanged —
but an explicit table now overrides it. Pin: `test_explicit_pricing_beats_the_hardcoded_provider_name`.

---

## R3 — Phase-cache keys were under-specified in two independent ways
**Severity: medium · Status: FIXED**

`phase_cache` documents "the key is the whole input, so a stale hit is
impossible". It was not the whole input.

**(a) The key used the model TIER, not the model actually called.**
`run_phase.sh` keyed on `PROVIDER:MODEL` where `MODEL` is the org-config tier
id (`claude-sonnet-4-6`), while the provider was invoked with `FINAL_MODEL`
(the `models_by_provider` mapping, e.g. `gpt-5-codex`). Re-pointing a tier at a
different provider model kept the same key, so the next run replayed a result
produced by a model that is no longer configured.

**(b) `run_key` was accepted by `lookup`/`store` and never hashed.** Artifacts
are stored under the *producing* run's paths (`testplans/<KEY>.md`,
`testdata/<KEY>/`). Two runs whose context bytes coincide — realistically, the
same text pasted under two ticket ids via `AIQE_INLINE_FILE` — shared an entry,
so the second run restored the **first key's** plan and never wrote its own.

**Fix.** `FINAL_MODEL` is resolved *before* the lookup and both call sites key
on it; `run_key` is fed into the hash at both call sites. Pins:
`test_wrapper_dispatches_through_the_port_and_keys_the_cache_by_provider`,
`test_cache_key_separates_run_keys`.

---

## R4 — `derived_writes` confined one branch and not its sibling
**Severity: medium (defense-in-depth) · Status: FIXED**

Same function, two branches. The testdata branch refuses a contract-chosen path
outside `testdata/`. The plan branch interpolated the run key straight into
`testplans/<key>.md`:

```
wrote: testplans\..\..\ESCAPED.md
escaped the checkout root: True
testdata traversal -> ['../../evil.json: refused — fixtures must live under testdata/']
```

**Reachability, stated honestly:** `pipeline.sh` validates `KEY` at entry
(`INVALID_KEY`, exit 64), so this is *not* exploitable through a normal run.
It matters because `derived_writes` is also a library entry point and a CLI,
and a guard that holds in one branch and not the one beside it is how the guard
gets lost in the next edit.

**Fix.** `safe_key()` applies the same shape `pipeline.sh` enforces, and
`materialize()` refuses anything else. The same one-line confinement was added
to `phase_cache.lookup`'s artifact-restore loop, which honoured whatever path a
cache entry named. Pin: `test_derived_writes_confines_the_plan_path_like_its_sibling`.

---

## R5 — A generic experimental-provider container with a hardcoded gate
**Severity: low · Status: FIXED**

`EXPERIMENTAL_PROVIDERS` was a tuple, but the opt-in check was hardcoded to
`AIQE_OPENHANDS_PROVIDER`. Adding a second experimental provider would have
silently unlocked it with OpenHands' flag.

**Fix.** It is now a `{provider: env_var}` map and the refusal names the
provider's own flag. Pin: `test_experimental_gate_is_per_provider`.

---

## R6 — "No event stream" was indistinguishable from "the agent said nothing"
**Severity: low · Status: FIXED**

`openhands_client.events()` returned `[]` on any HTTP ≥ 400, so a deployment
without an events endpoint produced the adapter message *"conversation finished
with no agent message"* — blaming the agent for a capability gap. It now raises
`LookupError` naming the endpoint and the likely config fix.

---

## Findings deliberately NOT fixed

* **`priced()` ignores cached-input tokens.** Estimates overstate slightly for
  providers with prompt caching. It is labelled `~` by construction and erring
  high is the safe direction for a budget estimate; correcting it needs
  per-provider cache pricing that is not in the table.
* **`resolve_llm` is in `ALL_PHASES` but has no `phases:` policy entry.**
  `validate()` therefore checks a phase `run_phase.sh` would `KeyError` on —
  but nothing dispatches it (it is a model-tier entry only), so the code path
  is unreachable. Noted rather than changed, because "make it consistent" here
  means deciding whether `resolve_llm` is a real phase, which is a design call,
  not a bug fix.
* **`spec_store` interpolates the key into `specs/<key>/…` without its own
  check.** Same class as R4 and the same entry-point guard applies. Left for a
  single shared key validator rather than a third copy of the regex.

## Verified as correct (attempted and failed to break)

* `provider_for` trims/lowercases and never substitutes a working provider for
  an unknown one — `'../../evil'` is refused by `check_assignment` before
  `adapter_path` is ever used to execute anything.
* `.env` / `aiqe.properties` are loaded at the top of `pipeline.sh`, so a
  Settings provider switch does reach `llm_runner` in CLI runs.
* Capability refusals outrank both model-mapping and experimental-opt-in
  refusals, so "this provider can never run this phase" is never buried.
* `parity_compare` handles the real gate-status casing (`committed`) and
  excludes any run with a simulated phase.
