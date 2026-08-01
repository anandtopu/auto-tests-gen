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

---

# Part 2 — Requirements still OPEN

## R12 — Enable `readOnlyRootFilesystem` on the deployment
**Priority: medium · NOT DONE**

The OpenShift manifests are otherwise well hardened: `runAsNonRoot`,
`seccompProfile: RuntimeDefault`, `allowPrivilegeEscalation: false`,
`capabilities: drop [ALL]`, resource limits, no literal secrets.
`readOnlyRootFilesystem` is absent.

**Why it is not just a one-line fix.** The pipeline writes to `catalog/`,
`testplans/`, `testdata/`, `specs/` and `knowledge/generated/` inside `/app`,
while only `reports/`, `workspace/` and `out/` are mounted volumes. Turning the
root filesystem read-only would break runs until every writable path is either
mounted or relocated.
**Acceptance:** audit the writable set, mount or relocate it, then set the flag
and prove a full `demo-pr` + `demo-jira` still commit.

## R13 — Bound XML entity expansion on the CI ingest
**Priority: low · NOT DONE**

XXE file disclosure is **not** possible — the stdlib parser refuses external
entities (`ParseError: reference to external entity`). Internal entity
*expansion* is not explicitly bounded; the 5 MB cap limits input, not what it
expands to. The endpoint is token-gated, so this is a DoS shape available only
to an authenticated CI sender.
**Acceptance:** parse with `defusedxml`, or disable DTD processing, and pin a
bomb-shaped payload that is refused. I did not detonate a real billion-laughs
to prove the exposure — crashing the machine to demonstrate a low-severity DoS
is a poor trade.

## R14 — Decide whether `resolve_llm` is a real phase
**Priority: low · DESIGN DECISION, not a bug**

`llm_runner.ALL_PHASES` includes `resolve_llm`, and `models:` gives it a tier,
but `phases:` has no policy entry — so `validate()` checks an assignment that
`run_phase.sh` would `KeyError` on. Unreachable today because nothing dispatches
it. Making it consistent means deciding whether it is a phase or a tier entry,
which is a design call, not a fix.

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
