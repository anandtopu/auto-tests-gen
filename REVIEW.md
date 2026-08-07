# Multi-Pass Review Report — AI QE Platform PoC (Build Edition)
Date: July 2026 · Reviewed build: phases B1–B5 executed against the in-repo demo estate.

## Pass 1 — Functional
| Check | Result |
|---|---|
| Routing golden tests (5) | ✅ pass |
| Benchmark fixtures (PR contract fan-out, JIRA component routing) | ✅ routing_ok on both |
| Adapter conformance (7 adapters, verb coverage + unknown-verb exit 64) | ✅ pass |
| Shell + Python syntax across the repo | ✅ clean |
| Workflow A end-to-end (mock LLM, real everything else) | ✅ generated tests executed against live app; gate committed |
| Workflow B end-to-end | ✅ plan + canonical data + tests committed; JIRA/Slack/telemetry fired |
| Catalog bootstrap live run (2 repos) | ✅ 3/4 tests auto-mapped @0.95 via contract/route/git evidence; planted legacy test correctly ORPHANED |

**Findings fixed during the pass:** F1 relative app path in demo config (off by one level) · F2 background app server held tool/CI pipes open → redesigned to scoped `with-env.sh` (up → test → guaranteed teardown via trap) — the correct pattern for CI runners generally · F3 mock clone missing parent dirs · F4 `compose_file` path fragile across workspace layouts → schema changed to `app_repo` + `app_entry` with resolution order (workspace/src → demo) · F5 gate "no changes" mislabeled as "committed" → machine-readable `GATE_STATUS=` protocol.

## Pass 2 — Architecture conformance (code vs. architecture v2.1)
| Principle (doc §) | Verified in code |
|---|---|
| Gate is the only push (§4.1-4) | `grep -rn "git push"` → engine/gate/gate.sh only ✅ |
| Born-mapped enforcement (§5.9.3) | gate step 2; adversarial test ADV-UNMAPPED exits 4 ✅ |
| Rules-first resolution, clarify below threshold (§5.8.2) | resolve.py + pipeline clarification branch; golden `test_jira_unmapped_asks_human` ✅ |
| Coverage maps generated, never hand-edited (§5.9.1) | regen_coverage.py rewrites `covers[]` from catalog; demo proved regeneration ✅ |
| Partial success per test repo, honest reporting (§5.8.5) | pipeline per-repo gate loop with committed/no-changes/quarantined states ✅ |
| Ports & adapters, engine vendor-free (§5.10) | pipeline calls SCM/TRACKER/NOTIFY/TELEM/PHASE functions only; mock and real adapters interchangeable — proven by running both ✅ |
| Framework-agnostic gate (§5.8.6) | lint/test commands read from `.ai-qe/config.yaml` (node-test in demo, playwright in templates) ✅ |
| G5 env provisioning closed | `test_env` schema + `bin/with-env.sh`; compose (hermetic) and shared modes ✅ |
| ⚠ Deviation | Phase JSON-contract schema validation runs in `run_phase.sh` (real LLM path) but mock stubs bypass `extract_contract.py`; acceptable for stubs, noted for parity |

## Pass 3 — Security / reliability (adversarial, now permanent: `make test-gate`)
| Attack | Expected | Result |
|---|---|---|
| Planted credential in generated spec | blocked, exit 3 SECRET_PATTERN | ✅ |
| Agent writes outside test paths (src/) | blocked, exit 2 SCOPE_VIOLATION | ✅ |
| New spec without catalog sidecar | blocked, exit 4 UNMAPPED_TEST | ✅ |
| Failing generated test | blocked, exit 5; verified NOT committed | ✅ |
Also verified: env teardown guaranteed via trap even on failure paths; idempotent onboarding (re-registering an existing repo is a no-op).

## Pass 4 — Integration readiness ("integrate with other repositories once ready")
| Check | Result |
|---|---|
| `bin/onboard.sh` registers a new **source** repo (payments-api, Bitbucket) | ✅ registry entry written; goldens still green |
| `bin/onboard.sh` registers a new **test** repo (e2e-api-tests-2) + triggers bootstrap when repo material exists | ✅ |
| Onboarding is idempotent | ✅ "already registered" no-op |
| Post-onboard regression (goldens + both fixtures) | ✅ all green with 6 source + 3 test repos registered |
| Templates + onboarding docs updated to new `test_env` schema | ✅ |

## Pass 5 — Historical real-LLM parity run (July 2026; Pass-5 revision)
Executed via `make parity-pr` / `make parity-jira` (`AIQE_MOCK=1 AIQE_REAL_LLM=1`: real
`claude -p` phases — Haiku triage, Sonnet 4.6 generate/plan/validate — against the demo
estate with mock adapters). **Both workflows green end-to-end**; total LLM cost ≈ $1.90.

This proves the Pass-5 revision only. Later engine and provider changes require a
fresh, version-stamped parity run before making current-head quality or cost claims.

| Check | Result |
|---|---|
| Workflow A: triage → generate → validate → gate | ✅ 7 boundary tests generated, executed against the live app, committed |
| Triage quality | ✅ correct `update` classification, exact catalog test_ids, contract fan-out reasoning |
| Workflow B: analyze → plan → data → generate → validate → gate | ✅ 6-scenario plan (incl. "extend PROJ-88" — update-vs-create working), canonical data, 5 tests passed, 1 repair loop exercised, committed |
| Never-guess behavior | ✅ 6 open questions raised (stacking semantics, missing response schemas, 400 body format) instead of invented assertions |
| JSON contract extraction + schema validation (real path) | ✅ after fixes below — closes the Pass 2 deviation for the real path |

**Parity findings (all fixed in-flight):**
P1 `triage`/`analyze` max_turns 5/8 too tight for real tool use → 12 · P2 triage was never
given the changed-file list or catalog slice as context · P3 phases ran with cwd=workspace
while prompts reference root-relative paths (every documented path missed) · P4 all seven
contract schema files contained a literal trailing `\n` — invalid JSON, never caught because
mocks bypass validation · P5 gate passed a newline-separated spec list to `bash -c`, executing
file 2+ as shell commands (surfaced only when the real LLM updated multiple specs) ·
P6 `extract_contract.py` read files with cp1252 on Windows · P7 contract extraction regex
grabbed the last brace-blob (often a code snippet in prose) → rewritten to parse the last
valid JSON object carrying the schema's required keys.

## Pass 6 — Post-H1 multi-pass review (July 2026: code scan + deep feature review + docs audit)
Three parallel review passes over the estate after the H1 features landed (budgets, Trace,
PR coverage-delta comment, SSO, properties config, per-repo Stash, factory reset). All
confirmed findings fixed in-flight and pinned by new/updated tests; full suite green after.

**Deep review of the H1 modules (13 findings, 11 fixed):**
F1/F2 `source .env` neither exported values to children nor honored the documented
precedence (`aiqe.properties < .env < explicit env`) — on the CLI path, `.env`-only
budgets/credentials never reached adapters or `budget.py`, and a stale `.env` clobbered
Secret-injected env. Replaced with two defaults-only `export` emitters
(`props_file.py dotenv-defaults` then `shell-defaults`; first-fill wins) ·
F3 the budget guard could fire between validate and the gate (at the advisory critic),
aborting a fully-paid-for run with exit 77 — critic now runs outside the PHASE wrapper
(still metered) · F4 `AIQE_COST_LEDGER` override was never truncated at run start ·
F5 **Windows path traversal**: `/runs/..\..\.env` served arbitrary files (posix `.name`
doesn't split on backslash; the pathlib join does) — strict-charset basename now enforced,
regression-tested · F7 `/hooks/openhands/*` ingestion broke the day UI auth was enabled —
hooks now gated on `AIQE_HOOK_TOKEN` (receiver contract) instead of UI auth ·
F8 `settings_store._parse` corrupted quoted secrets containing `#`/`'` that its own
`save()` wrote (round-trip now tested) · F9 a digit-leading properties key emitted invalid
bash and killed every run at startup · F10 **Stash/GitHub/Bitbucket `fetch_file` mapped
every failure (expired token, outage) to exit 3 "file absent"**, which made guidance_sync
delete the cached estate guidance — now 404-only; transport/auth errors exit 1 ·
F11 `clear()` rmtree'd the very lock dirs it was holding, voiding its interleave
protection — held `*.lock` dirs are now preserved during the wipe · F12 fs_lock broke
60s-old locks even when the owner PID was alive (long clears) — liveness check added ·
F13 the PR comment's "no noise" guard never fired (gate rows always exist) — all-`no_changes`
+ zero tests is now silent. Deliberately NOT changed: explicit `by` still beats the SSO
identity on approvals (delegation is the documented, test-pinned contract).

**Estate-wide code scan (14 findings, all fixed):**
S1 the resolver's `skip` verdict was dead code — a docs-only PR ran the full LLM phase
chain (real spend) and posted a success build status; the pipeline now exits on skip
(golden-pinned) · S2 `out/*.contract.json` was never cleared at run start, so run records
absorbed phases from the PREVIOUS run (a pr record carried the jira run's analyze/testplan);
`tests` mode's missing-snapshot fallback could shape generation with a different key's plan
(now fatal) · S3 the catalog slice fed to phases globbed only `catalog/e2e-*.jsonl` — any
differently-named test repo lost duplicate-prevention context · S4 `run_bootstrap.sh`
hardcoded the GitHub adapter and, on a failed clone, truncated an existing catalog to empty
(then `covers` regeneration silently unrouted the repo) — adapter now resolved like the
pipeline, failed clone is fatal · S5 registry writes outside `fs_lock` in `repos.py`,
`onboard.sh`, and the `regen_coverage` call sites — all locked + atomic now ·
S6 `eval/run_fixture.py` used plain `"bash"` (WSL stub trap) and leaked its temp file ·
S7 JIRA-keyed `plan`/`tests` runs never commented on the ticket (summary, budget abort,
clarification were `jira`-mode-only) · S8 the critic's `|| { }` failure handler was dead
code (set -e suppression inside PHASE) — rc now captured explicitly · S9 `harvest_facts.py`
crashed (KeyError) on a backend repo registered without `contract` (`Path/""` == `Path`) ·
S10 `regen_coverage.py` crashed on a blank catalog line · S11 `with-env.sh` leaked one temp
log per gate invocation · S12 `integration_check --json` always exited 0, masking hard
failures in CI · S13 `email_notify` CLI swallowed option values as positionals ·
S14 the gate's born-mapped check matched the spec path as a regex substring — now a
fixed-string, quote-delimited JSON-value match.

**Docs audit (22 findings, all applied):** exit-code table corrected (7 = PUSH_FAILED,
77 = BUDGET_EXCEEDED documented), Trace view documented across user-guide/README/
getting-started/architecture/diagrams, dashboard view counts unified against the
rendered view set at that revision, budget
enforcement + PR comment added to the workflow diagrams (order fixed: set_status then
comment), factory reset + properties layering + SSO documented in user-guide/deployment/
diagrams, adapter verb lists completed (7 verbs), stale KPI rows and persona text in
product-direction refreshed, lock-break time corrected to 90 min.

## Pass 7 — Test-gap closure + follow-up review (July 2026)
Pass 6 fixed 27 bugs but pinned only 5 with tests. This pass added the missing
32 regression tests (test_pass6_regressions.py, test_hooks_auth.py, plus
extensions) and ran a fresh review over the newest modules — 11 further
findings, all fixed:

**Caught by the new tests immediately:** the budget guard was DEAD on this
checkout — an aborted real-LLM run left `out/triage.json` behind carrying
`total_cost_usd`, mock phases never overwrite phase transcripts, so
`phase_cost` read every phase as "metered at $0" and exit-77 never fired.
Pipeline now clears `out/*.json` at run start (pinned).

**Review findings fixed:** fs_lock waiter busy-spun at 100% CPU forever when a
stale lock couldn't be released (falls through to the timeout now) and a
recycled PID wedged stale-break permanently (HARD_STALE_S=1h ceiling) ·
`fetch_file` in all three adapters treated a repo-level 404 (how GitHub/
Bitbucket answer for repos a token cannot SEE) as "file absent", which made
guidance_sync drop the cached estate guidance — a bare 404 now requires the
repo itself to answer 200 · `tier.py > catalog/x.jsonl` truncated the catalog
before tier ran (write-then-move now) · spec_exemplars: pathological relative
imports crashed the profile (silently disabling the feature for the run),
out-of-repo imports crashed relative_to, `*.test.js` estates were misread as
"no approach", helper tie-break was hash-order nondeterministic ·
openhands_agents accepted a missing target/PR and built broken pipeline
commands · demo_data left symlinked dirs behind while counting them removed ·
`pipeline.sh bogus KEY` silently ran the JIRA branch (INVALID_MODE exit 64) ·
fetch_file temp files leaked on EPIPE (trap cleanup) + bitbucket fetch_file
follows redirects like its sibling verbs.

Functional/E2E coverage added along the way: the real gate running in a temp
git repo (superstring born-mapped rejection), a real 401 HTTP server against
the LLM check, the dashboard /hooks auth truth table against a real server
process, bootstrap clone-failure catalog preservation, with-env teardown.
Suite: 429 passing.

## Pass 8 — UI-feature-set review + data-quality audit (July 2026)
Data-quality audit of every live store came back CLEAN (records/diffs/reviews/
queue/plans/catalog/registry/AGENTS.md all coherent); the one gap — unbounded
queue history — closed with `work_queue.prune_done` wired into `qa.py prune`.
Review of the post-Pass-7 modules: 9 findings, all fixed. Medium: /api/
pr-coverage crashed on one malformed run record (records are written
non-atomically — defensive parse now); curated_guidance.save lacked fs_lock +
atomic write (a torn file would be merged into the estate AGENTS.md); the E2E
drain test executed the USER'S real queue in forced mock mode (now isolated to
a tmp queue file); the server curated-roundtrip test left test content in the
committed AGENTS.md (finally-regen added). Low: plan-only queue items conflated
with full runs in the fetched-items marking (mode-aware now, + Plan-queued
button state); the OpenHands inline path launched an agent against a
nonexistent ADHOC ticket (a real key is required; pasted-only text keeps the
inline-queue path); curated repo names re-validated against the charset before
becoming path segments (defense in depth incl. drop()'s rmtree); a failed
curated load kept the previous repo's content in the editor (cross-repo save
hazard — cache reset on error); non-string JSON fields crashed the
/api/openhands/agent handler thread (typed 400 now) and the description is
capped at 20KB. Suite: 451 passing.

## Open items (ticketed, not blocking)
1. **Historical Claude parity completed for Pass 5.** Full `AIQE_MOCK=0` rollout still needs estate credentials, and current HEAD still needs a parity refresh.
2. ~~Mock stubs bypass `extract_contract.py`~~ — **closed 2026-08-05.** The mock harness now wraps each stub's contract as a provider-shaped reply (prose around a fenced JSON block, `out/<phase>.mockresult.json`) and runs the SAME extractor + schema check the real path uses. A stub that drifts from its schema fails the demo loudly instead of silently: measured by renaming a required key in the analyze stub, `make demo-jira` goes from exit 0 to exit 2 with `CONTRACT REJECTED ... fix the stub, not this check`. The wrapper is deliberately NOT named `out/<phase>.json` — `budget.record()` harvests that when present, and a mock file would be recorded with basis `reported`, i.e. a simulated run reporting a MEASURED $0. Pins: test_mock_contract_extraction.py (9).
3. Playwright execution unproven in this sandbox (browser CDN blocked) — framework abstraction verified via node-test; validate Playwright path in week 1 of real rollout.
4. OpenHands Path-1 live wiring (weeks 3–4 of the delivery plan); Path-2 mechanics fully proven.
5. **BACKLOG — refresh real-LLM parity on current HEAD for the four quality claims mock cannot test.**

   *Status (re-checked 2026-08-05): still blocked, same error.* A single-call probe returns
   `Failed to authenticate: OAuth session expired and could not be refreshed`.
   **Unblock with** `claude login` in an interactive terminal, **or** put
   `ANTHROPIC_API_KEY=...` in `.env` (the pipeline exports it since the
   config-layering fix). Then `make parity-pr` (~$0.3) and `make parity-jira` (~$1.6).

   Deliberately *not* launched while blocked: `parity-pr` runs
   `AIQE_MOCK=1 AIQE_REAL_LLM=1`, so the first phase would fail on auth, abort the
   run, and write a **quarantined run record** — trading the estate's 100% commit
   rate for a reproduction of a known error.

   Four claims ride on this run. Each is proven *mechanically* under `AIQE_MOCK=1`
   and unproven *qualitatively*, because the mock phases return fixed output:

   | Claim | Mock proves | Only a real run can show |
   |---|---|---|
   | Existing-approach exemplars | `out/repo-conventions.md` is built and passed to every generate AND validate call | that a generated spec actually imports the repo's own helpers and matches its assertion style — and that the critic raises no `new-approach` findings |
   | Per-repo generation fan-out (§5.8.8) | labeled contracts, the merge, containment of a per-repo failure, the off-switch | that an agent confined to one repo mirrors that repo more faithfully than one holding three repos' conventions at once — the correctness argument the fan-out was built on |
   | Adversarial plan review (§5.8.9) | adversary → arbiter → plan contract replacement, and that a failure leaves the authored plan standing | that the adversary raises gaps a real author genuinely missed. The mock returns the same two gaps every time: that is plumbing, not judgment |
   | Generated-test reviewer (§5.8.10, B6) | the production contract boundary catches all four scripted defect classes and approves the clean control; every number is labelled `SIMULATED` | the configured reviewer catches the same seeded defects without false-rejecting the clean fixture; run `make reviewer-eval-real` after the same parity authentication is restored |

   Run the first three together with the parity commands. Measure the fourth on
   the identical authentication path with explicit `make reviewer-eval-real`.

**Verdict: build phases B1–B5 complete; the mechanical suite is green and historical Pass-5 parity was green. A current-head real-LLM quality refresh remains blocked on authentication.**

## Pass 7 — end-to-end UAT (2026-07-30, ~70 adversarial probes)

Negative-input, lifecycle-abuse and discrepancy hunting across the API surface,
queue intake, plan lifecycle, CI ingest, state bundles, settings, CLI and the
live UI. Seven findings, all fixed the same day (pinned by
`registry/tests/test_uat_fixes.py`):

1. **`/api/repos/scope` destroyed scope on a missing field** — `apps` defaulted
   to empty, so a typo'd payload silently cleared a hand-managed scope and
   answered ok. The field is now required; clearing takes an explicit `""`.
2. **Phantom review-board rows** — `/api/review` (and `qa.py mark|release`)
   accepted any key. `review_state.require_known()` now refuses a key with no
   run record, no plan state and no existing entry.
3. **Intake validation gap** — the pasted-PR-URL path refused unregistered
   repos, but the plain name+number path (wizard form, API, TaskEvent webhook)
   accepted anything and failed minutes later in a background runner.
   `work_queue.add` now validates repo registration (pr) and key charset
   (jira/plan/tests) at intake, matching the pipeline's INVALID_KEY contract.
4. **Quarantine residue** — `unquarantine` wrote `"quarantined": false` into
   tracked catalog JSONL (permanent git noise); the lift now pops the tag,
   restoring the original bytes.
5. **Raw KeyErrors as API errors** — `{"error": "'target'"}` became
   `missing field: target` (`_err()` in dashboard_server, all 28 sites).
6. **Note enforcement was UI-only** — both `review_state.set_status` and
   `plan_state.set_status` now refuse `changes_requested` without a note.
7. **Silent-parameter inconsistencies** — `/api/trace?key=X` 404s when nothing
   is recorded; `/api/repos/sync?repo=X` filters (404 on unknown) instead of
   ignoring the parameter.

Held up under abuse without changes: gate attack suite, CI-ingest security
(token, 5 MB cap, parse errors, honest matched/unmatched), flake detection and
quarantine proposal, state-bundle integrity (no credentials, per-file checksum
rejection of tampered members, lock refusal, merge-never-destroys), settings
write-only secrets, approved-plan queue guard + edit-revokes-approval +
version snapshots + diff-since-approval, queue dedupe and actionable failure
reasons, and the full UI (zero console/network errors).

## Pass 8 — cost-stack adversarial UAT (2026-07-30, story 7.3)

Probes against the retrieval/reuse/spend stack (cost-reduction slices 1–7).
**All five held; one became a standing pin** (`test_context_scope.py::
test_every_assembly_carries_the_data_framing`):

1. **Poisoned chunk** — instruction-shaped text planted in synced guidance
   rides into the scoped context as expected; the data-framing preamble and
   the `missing_context` escape hatch are present in every assembly (pinned).
2. **Vector store corruption** — a garbage `vectors.db` is quarantined
   (`.corrupt-<ts>`) and rebuilt from chunks; queries fall back. (Pinned since
   slice 3, including the Windows close-before-rename case.)
3. **Reuse threshold abuse** — a non-numeric `reuse.plan_threshold` falls back
   to the conservative 0.80; below-threshold candidates are refused ("no
   candidate beats a stretched match").
4. **Envelope bypass via queue force** — `force=true` overrides only the
   approved-plan guard; the envelope warning still attaches, and the
   in-pipeline budget guard is untouchable from intake.
5. **Torn telemetry** — a torn run record is quarantined loudly by fs_lock and
   skipped by cost-report; a torn baseline silences the regression alarm
   rather than crashing the nightly.

## Pass 9 — spec-driven development UAT (2026-07-31)

The SDD stack (8 slices) went through the same adversarial bar:

1. **Forged scenario id** — a test claiming an id outside the signed spec is a
   strict-mode violation (pinned; the gate names the file and the id).
2. **Waiver abuse** — expired waivers refuse with the expiry named; a live
   waiver satisfies; both pinned. Live proof: strict gate REFUSED PROJ-301's
   two uncovered approved scenarios with actionable findings, then COMMITTED
   once they carried reasoned waivers.
3. **Arbiter stripping** — a re-emitting arbiter cannot demote a structured
   spec (merge-fold inherits by id; the mock arbiter deliberately drops fields
   so every demo exercises it).
4. **Silent divergence** — a free-form edit that contradicts the rendering
   supersedes the spec visibly (.superseded-<ts>); approval signs the yaml
   sha on the history line.
5. **Guess-instead-of-ask** — a blocking ambiguity stops the chain (exit 65)
   with the question on the ticket; non-blocking ones flow to the editor.
6. **Constitution rot** — every non-negotiable clause must name a live test
   pin or the build breaks (test_constitution.py).

Both gates ship OFF by default; strict enforcement is a two-step rollout
(warn-mode sprint first) — pinned so a config default cannot skip it.

Remaining honest gaps (all share the parity-auth blocker, REVIEW.md item 5):
the QUALITY delta for scoped/reused output (`make eval` checks retention +
token delta — 58% avg reduction — but not generation quality), the cache-probe
verdict, the measured cost baseline, and every dollar-denominated saving.
Levers gated on quality stay default-OFF until those runs land.
