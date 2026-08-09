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
is now 7 attacks (11 assertions as run).

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

---

# Pass C — state files, locking, and the server API

Same method: probe the claims, don't confirm the pins. Two defects, both in
code whose *purpose* is to prevent exactly the failure it allowed.

## C1 — An unreadable state file was reported as an empty one
**Severity: medium-high (silent loss of human decisions) · Status: FIXED**

`fs_lock.read_json_guarded` exists to stop silent state loss. Its docstring is
explicit: returning `default` for a damaged file "is the silent data-loss path
(the next save overwrites real data)". It handled that carefully for a corrupt
file — quarantine the bytes, warn loudly on stderr — and then did the exact
forbidden thing one branch below, for `OSError`:

```python
except OSError:
    return default          # sharing violation, EIO, full disk...
```

The `not path.exists()` check happens earlier, so reaching that branch means
**the file is there and could not be read**. Reproduction — one transient error
destroys a human's plan approval:

```
before: {"PROJ-301": {"status": "approved", "history": ["human sign-off"]}}
read_json_guarded returned: {}
after : {}
quarantine file created? NO
```

**Fix.** Retry briefly (transient causes clear in milliseconds), then **raise**.
A loud failure on one surface is recoverable; a silently emptied state file is
not. Corrupt-file quarantine and absent-file defaults are unchanged, and all
four paths are pinned:

```
A. persistent read failure -> raised;  state intact
B. transient failure       -> {'PROJ-301': {'status': 'approved'}}
C. corrupt file            -> quarantined as c.json.corrupt-<ts>
D. absent file             -> default
```

## C2 — Bundle-import containment was a string prefix, not a path test
**Severity: medium · Status: FIXED**

```python
target = (ROOT / rel).resolve()
if not str(target).startswith(str(ROOT.resolve())):
    raise SystemExit(f"bundle contains an unsafe path: {rel}")
```

A **sibling** directory whose name merely starts with the root's satisfies the
prefix while living entirely outside the checkout:

```
../evil.txt                        accepted=False truly_inside=False
../auto-tests-gen-evil/payload.txt accepted=True  truly_inside=False  <-- outside
../auto-tests-genX/payload.txt     accepted=True  truly_inside=False  <-- outside
```

The module's own header calls a bundle untrusted input — they get emailed and
attached to tickets. **Fix:** containment is a path relationship
(`root in target.parents`), never a string prefix. A real bundle still
round-trips (`merge import: would write 0 file(s), kept 20 existing`).

## Checked and found correct

* **Lock coverage.** Every read-modify-write cycle in `plan_state`,
  `review_state`, `work_queue` and `openhands_events` runs inside `fs_lock`.
  Checked against the code, and now pinned so a new unlocked mutation fails the
  build rather than losing a decision in a race.
* **Atomic writes.** `write_json_atomic` writes a same-directory tmp and
  `os.replace`s it, so a crash leaves the old or the new file, never a torn one.
* **Lock breaking.** Stale locks need age *and* a dead owner, with a hard
  ceiling for Windows PID reuse — and the owner is re-verified immediately
  before the break, so a waiter that already re-acquired is not torn down.
* **No path traversal via repo names.** `/api/repos/curated` and friends look
  unvalidated at the transport layer, but `curated_guidance` requires the repo
  to exist in the registry and restricts filenames to an allow-list. Validation
  lives at the domain layer, which is the right place for it.
* **Auth fails closed.** With `AIQE_SSO_HEADER` set, a missing header is 401
  (Bearer token still works for CLI clients). `do_POST` gates too, and
  `/hooks/*` has its own token contract that also fails closed when UI auth is
  configured but no hook token is set.

## Not treated as defects

Token comparisons use `==` rather than a constant-time compare, and the
first-visit token travels as a query parameter before becoming a cookie. Both
are real properties, neither is worth changing for a server the docs place
behind a reverse proxy or on localhost — flagging them as findings would be
noise, so they are recorded here instead.

---

# Pass C, part 2 — an adversarial suite for the state layer

`make test-state` (`tests/state-adversarial.sh`), 10 attacks, wired into
`make review`. It runs fully isolated: every store is pointed at a temp dir via
its documented env override, so the real estate is never touched.

It earned its keep immediately — it found C3 below, which no existing pin could
have caught because the failure only appears under concurrency.

## C3 — A contended lock raised an exception `lock()` does not catch
**Severity: medium · Status: FIXED**

On Windows, `mkdir` on a lock directory in a PENDING-DELETE state raises
`PermissionError` (WinError 5), **not** `FileExistsError`. Measured under 6-way
contention:

```
FileExistsError:183          x2088
PermissionError:5            x39        <- ~1.8% of acquisitions
```

`lock()` caught only `FileExistsError`, so this escaped the retry loop
entirely: no wait, no timeout, just an exception thrown inside whatever was
mutating a state file. It is now treated as "taken, try again", and the
original error is kept so a REAL permission problem still reports itself in the
timeout message instead of masquerading as contention.

## Two corrections worth recording

**A speculative fix made it worse, and measurement caught it.** The first
attempt retried the `rmdir` in `_release`. A/B against the original, 6 writers
x25:

```
ORIGINAL (single rmdir attempt)    losses 7/8
retry loop                         losses 8/8      <- worse
```

A retry can outlive our ownership and delete a lock a waiter has since
acquired, putting two writers in the critical section. Reverted, with the
measurement recorded in the code so it is not re-added.

**The first version of the concurrency attack was mis-calibrated.** Six writers
x25 in a tight loop does time out — but across every configuration tried the
state file was **never once corrupt** and no decision was ever half-written.
That is a throughput limit, not a correctness failure, and an attack that
failed on load would report "corruption" when the truth is "slow". The attack
now runs at realistic contention (3 writers — a dashboard thread, the queue
runner, a CLI call): 0/10 losses at 0.14 s.

**The measured limit, for the record.** Acquisition is polled at a fixed 50 ms,
and a release that loses the `rmdir` race leaves an ownerless lock dir that
waiters may only break after `ORPHAN_GRACE_S` (5 s). Together those cap
throughput at roughly a few acquisitions per second under heavy contention, so
a workload needing ~100 acquisitions inside a 10 s timeout will start raising
TimeoutError. Nothing is lost or corrupted when that happens — the write simply
does not occur and the caller sees the timeout. Real contention on these files
is a handful of operations per user action, which is why this has never been
observed outside a synthetic hammer. Tuning `ORPHAN_GRACE_S` or the poll
interval was deliberately NOT attempted here: that constant guards a real
crash-recovery scenario and deserves its own analysis rather than a change
made to green a test.

## Not delivered: the API attack script

The equivalent suite for `bin/dashboard_server.py` was written twice — once as
`tests/api-adversarial.sh`, once as `registry/tests/test_api_adversarial.py` —
and blocked both times by the local endpoint-protection agent, which flags the
attack payloads (traversal strings, credential-bypass probes) regardless of
file format. Rewriting it a third time to get past that would be evading a
security control, so it was left undone pending an explicit exception rule.

The API layer is not unexamined — pass C probed auth-fails-closed, POST gating,
the separate `/hooks/*` token contract, and repo-name traversal by reading and
exercising the code, and the earlier UAT campaign covers its negative cases.
What is missing is the permanent regression suite.

## G1 — The pipeline waited on the gate forever

`engine/pipeline.sh` invoked `engine/gate/gate.sh` with no timeout. The two
other callers of the same script both bound it — `.openhands/hooks/gate-check.sh`
uses `timeout 300` — so this was the odd one out rather than a considered choice.

It matters because of what the gate does: it runs each test repo's own
`commands.{lint,test}`. Those commands are chosen by that repo's committers
(see the onboarding trust boundary in `docs/onboarding-new-team.md`), so "a
command that never returns" is an ordinary failure mode, not an exotic one — an
`npm test` that waits on a port, a lint watcher started by mistake.

Nothing else bounded it either, which is the part worth remembering:

- `budget.py` enforces `MAX_WALLCLOCK_MIN`, but it is checked **before each
  phase**, and the gate runs after the last phase. The run budget cannot end a
  run that is already inside the gate.
- `out/.pipeline.lock` breaks after 90 minutes. That frees the **lock** so the
  next run may start — the hung process keeps running, and the next run's gate
  can hang the same way behind it.

Fixed by resolving a `timeout` prefix once before the fan-out and applying it at
the call site. Two details that were not incidental:

- **A host without `timeout(1)` says so** (`WARNING: ... gate runs are
  UNBOUNDED this run`) instead of silently reverting to the old behaviour. C13:
  an unenforceable limit is never reported as an enforced one.
- **The empty prefix is expanded as `${GATE_TO[@]+"${GATE_TO[@]}"}`.** Under
  `set -u` a plain `"${GATE_TO[@]}"` on an empty array is an unbound-variable
  error, which would turn "this host has no timeout binary" into "every gate
  aborts" — a graceful degradation converted into a total outage.

And the reporting half, which is half the fix: `timeout(1)` exits **124**, and
124 was absent from `run_progress.EXIT_MEANINGS`. Bounding the call without
documenting the code would have rendered a killed gate as an unexplained number,
or invited the reading that the tests failed. They did not fail — they never
finished. The summary line and the exit table both say so now, and `ST` stays
`quarantined` because nothing was committed, which is true.

Verified end to end rather than by inspection: `make demo-pr` commits normally,
and the same run under `AIQE_GATE_TIMEOUT_SEC=1` reports
`gate TIMED OUT after 1s (exit 124) - nothing was established about these tests,
and nothing was committed` for both repos. The run record that produced was
deleted afterwards — a deliberately-killed run left in `reports/runs/` would be
counted by `eval/scorecard.py` as product failure, the same scorecard pollution
recorded in CLAUDE.md.

Pins: `registry/tests/test_gate_is_bounded.py` (6), plus a repair to
`test_run_progress.py::test_every_documented_exit_code_is_one_the_source_actually_emits`.
That pin searched the raw source for `exit <code>`, so my own summary string
`"(exit 124)"` satisfied it — deleting the branch that handles a timeout left it
green. It now strips comments and double-quoted strings before looking, and
accepts `-eq <code>` as evidence for codes we do not raise ourselves. Mutation:
8 mutations, 8 killed, including that one.

## Deferred: the secret scan cannot prove it read everything

The scan reads whole files rather than the diff (see the note above the
`CHANGED_TRACKED` block, which records why). It builds its input like this:

```
... | xargs -0 -r cat 2>/dev/null
```

`cat`'s failures are sent to `/dev/null` and its exit status is discarded by the
pipeline, so a file the scan could not read contributes no content and is
indistinguishable from a file containing nothing of interest. The gate then
reports no secret and commits. That is the C13 shape in a security control, so
it is written down rather than left as a comment nobody reads.

**Why it is deferred rather than fixed now.** The consequence needs a file that
exists, has content, and cannot be read. In this deployment the gate runs as a
single non-root user inside the container, scanning files that same user just
wrote, so a permission denial is not a scenario the design produces. The other
`cat` failures are benign and must stay silent — a path listed by
`git diff --name-only HEAD` because it was DELETED is supposed to contribute
nothing, and refusing on it would break every run that removes a spec.

Attempting to reproduce it on the development host failed: Windows ACLs
(`icacls /deny`) do not deny the file's own owner, so no unreadable-but-present
file could be created. Rather than harden the most security-sensitive file in
the repo against a scenario that could not be exercised, the finding is recorded
with its trigger.

**Trigger to implement.** Do it when any of these becomes true:

- the gate starts scanning files it did not write (bundle import, a shared
  volume, a repo mounted from elsewhere), or runs as a different user than the
  one that produced the workspace;
- a deployment target appears where read failures are plausible (network
  filesystem, quota, SELinux/AppArmor confinement);
- any read failure is observed in practice.

**Shape of the fix, so it is not re-derived.** Filter the list to regular files
that still exist (`[ -f ]`, which keeps deletions silent), record every file
whose `cat` fails to a gap file, and refuse with a NEW exit code — not 3, which
asserts a secret was found, and not 0. A definite finding still wins: report the
secret first, and report the gap only when the scan would otherwise have said
"clean". `run_progress.EXIT_MEANINGS` gets the new code in the same change, for
the reason G1 records.

## G2 — The gate trusted its caller to have validated its arguments

`gate.sh <KEY> <test_repo>` interpolates both arguments into a path
(`$REPORT_DIR/${KEY}-${TREPO}.log`) and into the commit message, and validated
neither. It was safe in practice because `pipeline.sh` checks its KEY at entry
and exits 64 — but the pipeline is not the only caller.
`.openhands/hooks/gate-check.sh` takes KEY from the environment
(`${KEY:-${AIQE_KEY:-stop-hook}}`), and anything invoking the gate directly
supplies both. This is the R4 shape already recorded in CLAUDE.md: one branch
confining while its sibling does not is how a guard gets lost, and this is the
component holding the push credential.

Both arguments are now checked before anything is written, with two arms:

- the charset is **byte-identical to pipeline.sh's** (`*[!A-Za-z0-9._-]*`), so
  the two components can never disagree about what a key is;
- a bare `.` or `..` is rejected **separately**, because `.` is a permitted
  character and `..` therefore clears the charset arm untouched while still
  being a path component that escapes a directory.

A speculated related defect was checked and withdrawn: `spec_store.validate_key`
already rejects `..`, `.` and `../x`, so `specs/<KEY>/` was never escapable that
way. Recorded because a suspicion that is not written down as *checked and false*
tends to get re-investigated.

Order matters and is pinned: validating after `REPORT_DIR` is resolved would
already have created the file the check exists to prevent.

Pins: `registry/tests/test_gate_validates_its_args.py` (6). Mutation: 6
mutations, 6 killed — including "guard rejects everything", which every other
assertion in the file would have passed while the product was broken.
`make test-gate` stays green (16 checks).
