# Multi-pass review, round 2 — the gate and the core engine

Round 1 (`docs/review-multi-llm.md`) covered the LLM Runner port and the cost
stack. This round deliberately moved to the code every run goes through and
that the newer work had drawn attention away from: `engine/gate/gate.sh`,
`engine/pipeline.sh`, and the constitution's own claims — checked
**empirically**, not by confirming that a pin exists.

That reframing is what found S1. The pins for "the gate is the only push path"
all passed, and they were all true. The gate was still the weakest point in the
system, because nothing asked *what the gate itself executes*.

---

## S1 — The gate executed commands an LLM phase was allowed to write
**Severity: critical · Status: FIXED**

The gate reads `commands.lint` and `commands.test` from the test repo's
`.ai-qe/config.yaml` and runs them:

```bash
LINT_CMD=$(python3 -c "... yaml.safe_load(open('$CFG'))['commands']['lint']")
...
bash -c "$LINT_CMD"
```

`.ai-qe/` was **on the gate's own writable-scope allow-list**. So a phase with
`Write`/`Edit` — `generate`, whose prompt context includes untrusted ticket and
PR text — could rewrite the config, and the gate would execute it in the *same
run*, with the gate's own authority. The gate is the component that holds the
push credential.

Reproduction, before the fix:

```
planted lint command: lint: "echo PWNED-BY-LINT > /tmp/aiqe-gate-pwned.txt; true"
GATE_STATUS=WOULD_COMMIT           <- no complaint at all
--- did the gate execute it? ---
PWNED-BY-LINT
```

The full chain is untrusted input → LLM phase → repo config → trusted executor.
It is the same failure the platform already names for ticket text ("data, never
instructions") — applied to a file instead of a prompt, and therefore missed.

**Fix — two independent guards**, because either alone leaves a gap:

1. **`.ai-qe/` is off the writable scope.** A run that touches repo config is a
   `SCOPE_VIOLATION` (exit 2). Nothing in a run has ever legitimately written
   it — repo config is the owner's, changed out of band by `bin/onboard.sh`.
   Without this, guard 2 alone would only delay the injection by one run: the
   gate would commit the malicious config, and the *next* run would execute it.
2. **The commands come from the COMMITTED file** (`git show HEAD:.ai-qe/…`),
   never the working tree — so a modification arriving by any other route still
   cannot steer the current run. A repo whose config is not committed is
   refused (exit 6) with the reason, rather than silently trusting the tree.

After the fix:

```
.ai-qe/config.yaml
SCOPE_VIOLATION                    exit=2
--- executed? --- (NOT executed)
```

and guard 2 shown independently:

```
working tree now says:  lint: "echo WORKTREE-VERSION"
gate reads from HEAD :  lint: "true"
```

`bin/with-env.sh` reads the same file from the working tree for `test_env`
(it launches the app under test). It runs at gate step 4, *after* the scope
check at step 1, so guard 1 covers it; its other caller, `spec_verify`, uses a
fresh read-only clone. Left as-is rather than adding a third config reader.

**Pinned permanently** in `tests/gate-adversarial.sh` as two assertions — the
attack must be refused *and* the planted command must never run. The gate suite
is now 6 attacks.

---

## Checked and found correct

These were probed with the intent of breaking them, and held:

* **Only the gate mutates git state.** A repo-wide search for
  `git commit|push|add` outside `engine/gate/` returns only
  `adapters/mock/scm.sh`, which builds a baseline inside a demo *copy*.
* **Generation fan-out containment.** A per-repo failure removes that repo's
  contract and continues; all-repos-failing returns non-zero rather than
  handing `validate` an empty contract; `merge_contracts` records the dropped
  repos in `fanout.skipped`, so a partial run is never reported as complete.
* **Filename charset restriction.** Changed paths are rejected unless
  `[A-Za-z0-9._/-]`, which is what makes the later `bash -c "$TEST_CMD $SPECS"`
  safe.
* **Born-mapped matching** uses `grep -qF "\"$spec\""` — a fixed, quote-
  delimited match, so a path's dots cannot act as regex wildcards and a
  superstring mention cannot satisfy it.
* **Run lock.** `mkdir`-based, trap-released, and the 90-minute stale break sits
  above the longest real phase chain, so a live run is never broken by a
  competitor.

## Observation, not a defect

A lock left by a *killed* run (younger than 90 min) makes every subsequent
pipeline test wait its full 120-second acquire window before exiting 75. That
is the designed trade-off — the alternative is breaking live runs — and
`registry/tests/conftest.py` already sweeps the >90-minute case and says so.
Worth knowing when a suite suddenly crawls: the cause is usually a killed run,
not the code under test.
