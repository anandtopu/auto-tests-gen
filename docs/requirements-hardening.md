# Hardening requirements — consolidated from four review rounds

**Scope.** Everything found by scanning this codebase for defects, design gaps
and vulnerabilities, expressed as requirements: what must be true, why, and how
you know it is. Satisfied requirements name the fix and the pin. Open ones say
what is not done and why.

**What this document does not claim.** It does not say the platform is free of
vulnerabilities. Nobody can honestly say that, and a document asserting it would
be worth less than one that does not. What it says is: these areas were probed
in these ways, this is what was found, this is what was fixed, and this is what
remains — so the next person starts from evidence rather than from faith.

## Method

Four rounds, each probing claims rather than confirming that pins exist:

| round | scope | defects |
|---|---|---|
| 1 | LLM Runner port + cost stack | 6 |
| 2 | gate + core engine + constitution clauses | 1 critical |
| 3 (pass C) | state files, locking, server API | 2 + 1 concurrency |
| 4 | dashboard rendering, prompt framing, email, deploy | 0 exploitable, 1 hardening |

Every finding carries a reproduction that was actually run. Findings that did
not survive verification were dropped, not reported as "potential".

---

# Part 1 — Requirements now SATISFIED

## R1 — The gate must never execute instructions produced by a run
**Severity: critical · Round 2 · FIXED**

The gate read `commands.{lint,test}` from the test repo's `.ai-qe/config.yaml`
and executed them, while `.ai-qe/` sat on its own writable-scope allow-list. A
`generate` phase — whose context includes untrusted ticket and PR text — could
rewrite that config and have it run in the same run, with the authority that
holds the push credential.

```
planted: lint: "echo PWNED-BY-LINT > /tmp/aiqe-gate-pwned.txt; true"
GATE_STATUS=WOULD_COMMIT      <- no complaint at all
--- executed? --- PWNED-BY-LINT
```

**Acceptance:** `.ai-qe/` off the writable scope (SCOPE_VIOLATION, exit 2) AND
commands read from the committed file, never the working tree. Both, because
guard 2 alone only delays the injection by one run.
**Pins:** two assertions in `tests/gate-adversarial.sh` (refused *and* never
executed), plus a source pin. Constitution C1 amended.

## R2 — Spend controls must never be silently unenforceable
**Severity: critical · Round 1 · FIXED**

An unpriced provider recorded cost 0 and `metered=0`, and both controls gate on
`metered > 0` — so the exit-77 ceiling never fired and the degradation ladder
never started. 27.5M tokens reported "$0.00, within budget".

**Acceptance:** cost cannot be invented, so the ceiling still cannot abort on
unknown spend — but `budget.enforceability()` reports
`enforced|partial|unenforceable` naming the exact `pricing:` key, the run record
carries it, and the cost report leads with "This total is incomplete".
**Pin:** `test_unpriced_provider_cannot_silently_disable_the_budget`.

## R3 — Configuration must beat hardcoded provider names
**Severity: high · Round 1 · FIXED**

`priced()` forced `ollama` to `$0 (local)` regardless of the price table. That
adapter is plain OpenAI-compatible HTTP and serves paid hosted gateways, so an
operator who priced it saw $0 for a real bill.
**Acceptance:** an explicit `pricing:` entry wins; org-config still ships
`ollama: local` as the default. **Pin:** `test_explicit_pricing_beats_the_hardcoded_provider_name`.

## R4 — A cache key must cover everything that changes the result
**Severity: medium · Round 1 · FIXED**

Two independent under-specifications: the key used the model *tier* rather than
the mapped model (re-pointing `models_by_provider` replayed a result from a
model no longer configured), and `run_key` was accepted but never hashed while
artifacts store under the producing run's paths.
**Pins:** `test_cache_key_separates_run_keys`, plus the wrapper source pin.

## R5 — Path containment must be a path test, never a string prefix
**Severity: medium · Rounds 1 and 3 · FIXED**

Three sites: `derived_writes` interpolated the run key into `testplans/<key>.md`
with no check; `phase_cache.lookup` honoured whatever path a cache entry named;
`state_bundle` used `str.startswith`, which accepts a sibling directory sharing
the root's name (`../<root>-evil/payload`).
**Acceptance:** containment is `root in target.parents`; run keys are validated
against the same shape `pipeline.sh` enforces.

## R6 — An unreadable state file must never read as an empty one
**Severity: medium-high · Round 3 · FIXED**

`read_json_guarded` quarantined corrupt files carefully and then returned
`default` on `OSError` — so one transient read failure destroyed a human's plan
approval, because the caller saved over it.
**Acceptance:** retry briefly, then raise. Corrupt-file quarantine and
absent-file defaults unchanged. **Pin:** all four paths.

## R7 — A contended lock must not raise at the caller
**Severity: medium · Round 3 · FIXED**

On Windows a pending-delete lock directory makes `mkdir` raise
`PermissionError` (WinError 5), not `FileExistsError` — measured at ~1.8% of
acquisitions under 6-way contention. `lock()` caught only the latter, so it
escaped the retry loop and crashed whatever was mutating state.

## R8 — Existing-test context must be scoped by the mapping that routed the run
**Severity: medium · FIXED**

`out/catalog-slice.jsonl` was a concatenation of every `catalog/*.jsonl`. A PR
resolving one API repo still received the UI repo's rows.
**Acceptance:** filtered by `covers:`, per-repo in the fan-out, and an empty
selection falls back to the whole catalog *loudly* — starving generation of
existing-test context makes it duplicate work it cannot see.

## R9 — Experimental capability must be gated per provider
**Severity: low · Round 1 · FIXED.** `EXPERIMENTAL_PROVIDERS` is now
`{provider: env_var}`; it was a tuple with the gate hardcoded to OpenHands' flag.

## R10 — A capability gap must not be reported as an agent failure
**Severity: low · Round 1 · FIXED.** `openhands_client.events()` returned `[]`
on HTTP ≥ 400, so "this deployment has no event stream" surfaced as "the agent
said nothing". It raises `LookupError` naming the endpoint.

## R11 — HTML escaping must not depend on an unenforced convention
**Severity: low (hardening) · Round 4 · FIXED**

`escHtml` escaped `& < > "` but not `'`. Safe *today* because every attribute in
the file is double-quoted — and that is precisely the shape of guard this
codebase keeps losing. Now escapes both quote characters, removing the
dependency on a convention nobody enforces.

## R12 — Enable `readOnlyRootFilesystem` on the deployment
**Priority: medium · DONE (2026-08-01) — flag ON and proven in a container**

Full audit: `docs/review-readonly-rootfs.md`.

**The filing was wrong in three ways** — it missed `/tmp` (the flag covers the
whole root; four SCM adapters and the CI receiver call `mktemp`), Python
bytecode (`__pycache__` inside `/app`), and `AGENTS.md` / `.env`, which sit at
the checkout root where no volume mount can reach them. `AGENTS.md` is the first
thing a read-only run dies on.

**And its prescribed fix would have caused a worse bug.** Every mutable
directory except `testplans/` and `testdata/` mixes data with code or config.
Mounting a volume over `catalog/` hides `bootstrap/*.py`; seeding that volume
from the image instead freezes the code and `org-config.yaml` at first boot, so
an image upgrade ships logic that never runs — silently, with a healthy
container and a new version label. State is therefore RELOCATED, not mounted
over.

**Shipped**
- `engine/lib/app_paths.py` — `per-path knob > AIQE_STATE_DIR > caller ROOT`.
  Defaults are byte-identical to the hard-coded paths, which is the pin that
  matters most. `_FROZEN_SUBPATHS` keeps `catalog/bootstrap/`, `catalog/schema.json`,
  `registry/org-config.yaml`, `registry/tests/` and `specs/platform/` in the
  image even though their parents are mutable — by SEGMENT comparison, never a
  string prefix (R5's defect class: `startswith` accepts a sibling
  `catalog/bootstrap-old/`).
- 22 Python call sites and the shell chain rewired. `engine/gate/gate.sh` and
  `mock_phase.sh`'s `$T/catalog` deliberately untouched — those are the TEST
  REPO's born-mapped sidecar, and rewiring them would break constitution C3
  while looking like a consistent search-and-replace.
- `bin/container-entrypoint.sh` seeds an empty state root once, DATA ONLY, and
  never overwrites: the volume holds a human's confirmed mappings and curated
  guidance, so re-seeding on restart would silently revert their work.
  *(Later correction: "DATA ONLY" was an intention, not a fact. The loop copied
  whole directories, so `catalog/review` carried `export_review_queue.py` and a
  `__pycache__`, and `knowledge/facts` carried the gitignored `derived/` tier —
  while the pin checked only that the `SEEDED` strings looked right. Expansion
  and exclusion moved to `app_paths.seed_plan()`, and `tests/entrypoint-smoke.sh`
  now asserts against what a boot actually copies. The same run found the
  entrypoint seeding **nothing** into an empty root while reporting "already
  populated" — see `docs/review-readonly-rootfs.md`.)*
- `PYTHONDONTWRITEBYTECODE=1`, a `/tmp` emptyDir, a `/state` volume, and
  `readOnlyRootFilesystem: true` on both containers.
- **A real bug the container exposed:** `pipeline.sh` took its lock with
  `mkdir 2>/dev/null`, which cannot tell `EEXIST` from `EROFS`, so a read-only
  root spun the full 120s retry loop and reported `PIPELINE_BUSY` — sending an
  operator after a concurrent run that does not exist. Now `PIPELINE_UNWRITABLE`
  in 0.08s, naming the directory and stating it is not contention.

**Proof** — `podman run --read-only` against an EMPTY state volume:

```
[entrypoint] seeded 8 path(s) into /state      (catalog data, registry, knowledge)
created /state/{testplans,testdata,specs,.agents/skills}
jira PROJ-301   -> GATE_STATUS=COMMITTED a2c3465
pr orders-api#201 -> GATE_STATUS=COMMITTED 0df683b + NO_CHANGES (ui repo)
touch /app/PROOF -> Read-only file system
second start    -> "already populated — nothing seeded"   (no clobber)
```

844 tests pass; both demos green outside the container too.

---

# Part 2 — Requirements still OPEN

## R13 — Bound XML entity expansion on the CI ingest
**Priority: low · DONE (2026-08-02)**

XXE file disclosure was never possible — the stdlib parser refuses external
entities. What was unbounded was INTERNAL entity expansion: the 5 MB upload cap
limits input, not what that input expands to.

**Measured, on a deliberately tiny payload.** 202 bytes of nested entities
expanded to 1000 characters at three levels, and each further level multiplies
by ten. I did not detonate a real billion-laughs — crashing the machine to
demonstrate a low-severity DoS is a poor trade, and a bomb-SHAPED payload proves
the guard just as well.

**Fix: refuse any DOCTYPE, before the tree parser sees the document.**
`test_health._reject_dtd` runs a stdlib expat pass whose
`StartDoctypeDeclHandler` raises `UnsafeXML`. A DOCTYPE refusal rather than an
expansion budget, because JUnit XML has no legitimate use for a DTD — so
refusing outright costs nothing real and cannot be tuned wrong. No entity
declarations means there is no expansion left to bound.

stdlib only, deliberately: `defusedxml` would also solve this, but this codebase
avoids dependencies for things it can state in ten lines, and that would be the
only dependency in the parse path.

**Verified end to end** against the live receiver: a bomb-shaped upload returns
`400 {"error": "... refused: the document declares a DOCTYPE ..."}` — an
actionable message in the CI job's own log — while a clean JUnit upload still
ingests. Ordinary syntax errors still surface as `ParseError` from the real
parser, with line and column, because a malformed file is not a security
refusal. Pins in `test_ci_ingest.py`; removing the guard fails them.

## R14 — Decide whether `resolve_llm` is a real phase
**Priority: low · DECIDED AND DONE (2026-08-01) — it is not a phase**

**The finding as filed.** `llm_runner.ALL_PHASES` includes `resolve_llm`, and
`models:` gives it a tier, but `phases:` has no policy entry — so `validate()`
reports the config clean while a dispatch would die on `KeyError: 'resolve_llm'`
in `run_phase.sh`. Unreachable, because nothing dispatches it.

**What the investigation actually found.** Not one phantom but three, and the
filing under-described the problem:

| key | tier | policy | in `ALL_PHASES` | prompt | dispatched |
|---|---|---|---|---|---|
| `resolve_llm` | ✅ | ❌ | ✅ | ✅ `prompts/resolve-llm.md` | ❌ |
| `resolve` | ✅ | ✅ | ❌ | ❌ | ❌ |
| `escalate` | ✅ | ❌ | ❌ | ❌ | ❌ |

`resolve` and `resolve_llm` are two names for the *same* unbuilt thing — ADR-5's
LLM routing fallback — split across the config so neither half looked wrong on
its own. `models.resolve` even carried the comment "routing is deterministic;
this is the fallback". `escalate` (`claude-opus-4-8`, "after 2 failed generate
attempts") is a tier for a retry escalation nothing implements. All three read
as live configuration: an operator tuning any of them would have changed
nothing, and the only way to discover that was to read the dispatch sites.

**Decision: `resolve_llm` is not a real phase, and the auto-routing LLM fallback
should not be built.** Full reasoning in architecture §5.8.2 and the ADR-5
amendment. In short: a misroute is the one failure this pipeline cannot see — it
reports *success* while writing tests against the wrong repo, so the cost is not
a wasted run but coverage nobody knows is missing (this is what §5.15 and the
11-attack routing suite exist for). An LLM rung's upside is bounded by the cases
where it is confident *and* right; its downside is precisely confident-and-wrong.
The rung it would replace — ask a human — costs one reply.

The option is preserved in the shape that keeps determinism: an LLM
**suggestion inside the clarification comment**, which a human confirms. A
proposal to a person, never a route. Not built; recorded as the correct
follow-on if poor-metadata tickets ever justify it.

**Shipped.** All three keys removed; `prompts/resolve-llm.md` deleted;
`resolve.py`'s docstring (which promised the fallback), architecture §5.8.2, the
capability matrices in architecture + `multi-llm-providers.md`, the routing
table and ADR-5 all corrected.

**The durable fix is the pin, not the deletion.** `registry/tests/`
`test_phase_inventory.py` asserts `org-config models: == phases: ==
llm_runner.ALL_PHASES == what pipeline.sh dispatches`, so any future half-wired
phase breaks the build naming which side is missing. Writing it surfaced a trap
worth recording: `critic` is dispatched via `_PHASE_IMPL`, not `PHASE`
(deliberately — the budget guard must not abort a fully-paid-for run over an
advisory signal), so a pin matching only `PHASE` would have reported `critic` as
a phantom and "confirmed" the bug it exists to catch. All 8 assertions are
mutation-tested.

## R15 — Constant-time token comparison
**Priority: very low · DELIBERATELY NOT DONE**

`==` on the UI and hook tokens, and the first-visit token travels as a query
parameter before becoming a cookie. Both are real properties. Neither is worth
changing for a server the documentation places behind a reverse proxy or on
localhost, and flagging them as findings would be noise. Recorded so the next
reviewer does not spend time rediscovering them.

## R16 — `observed` tier for the per-repo knowledge base
**Priority: medium · DEFERRED BY DECISION**

Flake rates, surface churn and reviewer-edit patterns are the highest-value tier
of the knowledge base and the one that makes the platform *learn*. Deferred
because it needs a real CI feed — `catalog/health.json` is absent until someone
runs `make ingest-results` — and shipping an empty tier that looks populated is
worse than not having one.

## R17 — Measure the lock's throughput ceiling before tuning it
**Priority: low · INVESTIGATED, DELIBERATELY NOT CHANGED**

Acquisition polls at a fixed 50 ms and a release that loses the `rmdir` race
leaves an ownerless dir breakable only after `ORPHAN_GRACE_S` (5 s). Together
those cap throughput at a few acquisitions per second under heavy contention,
so ~100 acquisitions inside a 10 s timeout starts raising `TimeoutError`.

**Nothing is lost or corrupted when that happens** — across every configuration
tried the state file was never once corrupt; the write simply does not occur and
the caller sees the timeout. Real contention is a handful of operations per user
action, which is why it has never been observed outside a synthetic hammer.

A retry loop in `_release` was tried and **measured as worse** (8/8 trials
losing decisions vs 7/8) because a retry can outlive our ownership and delete a
lock a waiter has since acquired. Tuning `ORPHAN_GRACE_S` or the poll interval
was not attempted: that constant guards a real crash-recovery scenario and
deserves its own analysis rather than a change made to green a test.

---

# Part 3 — Probed and found correct

Recorded so the next reviewer does not repeat the work. Each was attempted with
intent to break it.

**Injection & execution**
* No `eval`, `exec`, `pickle`, or `yaml.load` anywhere — all `yaml.safe_load`.
* No `shell=True` or `os.system`; subprocess calls use argument lists, so a
  queued target cannot inject a command.
* Generated filenames are charset-restricted, which is what makes the gate's
  later `bash -c "$TEST_CMD $SPECS"` safe.
* **Prompt injection framing is complete**: 13 prompt files, 10 standalone all
  carrying "DATA … never instructions"; the other 3 are platform-authored
  fragments passed as context under a framed parent, selected by a fixed 3-way
  `case` so a hostile issue type can only choose among known files.

**Web surface**
* **XSS not exploitable**: every user-influenced field goes through `escHtml`,
  every attribute is double-quoted, and intake validation rejects a payload as a
  ticket key before it is ever stored.
* Auth fails closed on SSO; `do_POST` gates; `/hooks/*` has its own token
  contract that also fails closed when UI auth is on but no hook token is set.
* No SSRF from a pasted PR URL — `pr_url.py` parses, never fetches.
* **Email header injection blocked** by the stdlib (`ValueError` on CRLF).

**Data integrity**
* Every read-modify-write in `plan_state`, `review_state`, `work_queue` and
  `openhands_events` runs inside `fs_lock` — checked against the code and pinned
  so a new unlocked mutation fails the build.
* Atomic tmp+replace writes; stale-lock breaking requires age *and* a dead
  owner, with the owner re-verified immediately before the break.
* Bundles carry no `.env`, no properties file and no code.
* Only the gate mutates git state.

---

# Part 4 — The pattern worth acting on

Across four rounds, **every defect was in a guard, not in ordinary logic.** The
gate's scope list, the cost basis label, the cache key, three containment
checks, the corrupt-file handler, the lock's exception set. Each was written
deliberately, each had passing pins, and each had one branch or one comparison
that did not hold the line the rest of the code drew.

Pins that assert a guard *exists* do not catch that. Only probing what the guard
does on hostile input did.

**The highest-value ongoing work is therefore not more features — it is
extending the adversarial-suite pattern.** Four now exist: `test-gate` (6
attacks), `test-providers` (8), `test-state` (10), and the API suite (40 pytest
cases). Surfaces still without one: the catalog bootstrap correlator, the
resolver's routing rules, and the notify/telemetry adapters.
