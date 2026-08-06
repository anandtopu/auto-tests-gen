# Use cases — what you came here to do

`getting-started.md` teaches you the demo. `user-guide.md` is the reference
manual. **This page is neither** — it is organised by the job you actually have,
so you can find your situation and follow it end to end.

Every command here is real and was run against this codebase. Where something is
blocked or deferred, it says so rather than pretending.

| I want to… | go to |
|---|---|
| update E2E tests because a service changed | [1](#1-a-pr-changed-a-service) |
| write tests for a new ticket | [2](#2-a-ticket-needs-test-coverage) |
| review a test plan before any code is written | [3](#3-review-and-approve-a-plan-first) |
| add a new repo to the platform | [4](#4-onboard-a-repo) |
| find out where coverage is missing | [5](#5-find-coverage-gaps) |
| deal with a flaky test | [6](#6-quarantine-a-flaky-test) |
| know what this costs, and spend less | [7](#7-see-and-reduce-llm-cost) |
| run on a local model instead of a paid API | [8](#8-switch-llm-provider) |
| prove ticket → test → commit for an audit | [9](#9-traceability-for-an-audit) |
| move an estate to another environment | [10](#10-move-your-estate) |
| work out why a run did not do what I expected | [11](#11-diagnose-a-run) |

**Two things worth knowing before any of them.** Nothing reaches a repo except
through the gate — no LLM phase can commit or push. And every run is mock by
default (`AIQE_MOCK=1`): you can try all of this without spending anything.

---

## 1. A PR changed a service

**You are:** a QE or a dev whose PR changed an API or UI surface.
**You want:** the E2E suites that cover it updated, without hand-writing them.

```bash
make run-pr REPO=orders-api PR=201        # real
make demo-pr                              # the same flow, mocked
```

**What happens.** The platform resolves which E2E repos cover `orders-api` from
the registry's `covers:` mapping, clones them, reads the actual PR diff, writes
or extends specs, runs them inside a provisioned environment, then the gate
decides whether to commit.

**What you get back.** A PR comment and a run record:

```
✅ e2e-api-tests-1: committed 3610891
➖ e2e-ui-tests-1: no_changes
🔍 critic (advisory): 0.86 accept
```

`no_changes` is a normal outcome — that repo's surface was not affected.

**Verify it:**

```bash
make status                               # recent runs and their gate results
python3 bin/qa.py artifacts PR-orders-api-201    # the generated code + spend
```

**If nothing was generated:** the resolver found no test repo covering that app
repo. See [use case 5](#5-find-coverage-gaps) — this is a mapping gap, not a
failure.

---

## 2. A ticket needs test coverage

**You are:** a QE picking up a story or bug.
**You want:** tests that match the acceptance criteria.

```bash
make run-jira KEY=PROJ-301                # real
make demo-jira                            # mocked
```

The ticket's text is treated as **data, never instructions** — a ticket cannot
tell the platform what to do.

**When the ticket is ambiguous**, the run stops rather than guessing:

```
NEEDS_CLARIFICATION: the discount cap is not specified for stacked promotions
```

It comments the question on the ticket and exits 65. Answer the question, re-run.
That refusal is the feature — a plausible guess is worse than a question.

**If you have no ticket** (a Slack thread, an email):

```bash
python3 bin/qa.py run-inline --file context.txt --key ADHOC-1
```

---

## 3. Review and approve a plan first

**You are:** a lead who wants to see the test plan before any code exists.
**You want:** a human gate between "what we will test" and "the tests".

```bash
make plan KEY=PROJ-301          # authors the plan, then STOPS
make plan-show KEY=PROJ-301     # read it
make plan-edit KEY=PROJ-301 FILE=edited.md      # optional: change it
make plan-approve KEY=PROJ-301  # sign it off
make plan-tests KEY=PROJ-301    # NOW generate
```

**Generation is refused until you approve:**

```
test plan for PROJ-301 is 'draft', not approved — review and approve it first
```

**Before you see it, the plan has already been challenged.** A read-only
adversary hunts for what the author missed (negative, boundary, authz, state,
cross-repo, data cases) and an arbiter folds accepted findings in:

```
adversarial review: 2 gap(s) raised, 2 high-severity, 2 accepted,
3 scenario(s) in the final plan
```

That happens *before* the approval gate, so it changes what you approve — never
whether you are asked.

**Editing an approved plan revokes the approval.** That is deliberate: a
sign-off applies to the text that was signed. Re-approve after editing.

Everything here is also in the dashboard's **Test plans** view, including a
diff-since-approval so re-approval reviews the change rather than the whole
document again.

---

## 4. Onboard a repo

**You are:** adding a service or an E2E suite the platform does not know about.

```bash
bin/onboard.sh source orders-api backend github org/orders-api orders api/openapi.yaml
bin/onboard.sh test   e2e-api-tests-1 api github org/e2e-api-tests-1 playwright-api
make test-routing                          # re-run the resolver goldens
```

For a **test repo**, onboarding also triggers the catalog bootstrap, which
extracts what each existing test covers and correlates it to app repos.

**Then map it.** `covers:` is generated from catalog evidence and must never be
hand-edited. What you *can* set is `scope` — the repo's declared responsibility:

```bash
python3 bin/repos.py scope e2e-api-tests-1 --set orders-api,payments-api
```

`covers:` becomes catalog evidence ∪ scope. Use `scope` when a repo is *meant*
to cover something it has no tests for yet — that is what makes the gap visible
instead of invisible.

**Teach it your conventions** (optional, and worth it):

```bash
python3 bin/repos.py notes e2e-api-tests-1 --file guidance.md   # prose
# structured facts (E2E repos): knowledge/facts/<repo>.yaml
python3 engine/lib/repo_facts.py show e2e-api-tests-1
```

A repo's own `AGENTS.md`/`CLAUDE.md` always outranks anything the platform
generates about it.

---

## 5. Find coverage gaps

**You are:** asking "what is not tested?"

```bash
make coverage      # app-repo × test-repo matrix, with warnings
make gaps          # harvested surface with NO test evidence
```

```
WARNING - no E2E coverage mapped for: admin-portal-ui, catalog-api, payments-api
NOTE - test repos with empty coverage (run bootstrap?): e2e-api-tests-2
```

Two different problems, and the distinction matters:

* **no coverage mapped** — nothing tests that app repo. Real gap.
* **empty coverage** — the test repo exists but its catalog is empty. Usually
  means bootstrap has not run, not that the tests are missing.

Uncovered surface is marked `[NO TEST]` in the estate knowledge, so generation
targets it first. The dashboard **Trace** view shows the same thing per scenario,
including approved scenarios with no test — the loudest line on an audit.

---

## 6. Quarantine a flaky test

**You are:** tired of a test that fails at random.

```bash
make ingest-results FILE=junit.xml        # feed CI results in
python3 bin/qa.py flaky                   # rank by failure rate
python3 bin/qa.py quarantine "e2e-api-tests-1::suites/orders/search.spec.js::paginates" \
  --note "fails under --parallel, see PROJ-412"
python3 bin/qa.py unquarantine "<test_id>"
```

CI can also POST JUnit XML straight to the receiver (token-gated, 5 MB cap).

**What quarantine does — and does not do.** It is a *catalog tag*. The platform
prints an exclusion line for you to apply in your own CI; **it never edits a test
repo's config.** The gate still gates changed specs. Nothing is silently disabled
on your behalf.

---

## 7. See and reduce LLM cost

```bash
make cost-report                 # by workflow, key, phase, model, provider
make cache-stats                 # phase reuse — calls avoided
```

**Read the labels, they are load-bearing:**

| shown | means |
|---|---|
| `$0.30` | the provider reported this — a real figure |
| `~$0.05` | estimated from the price table |
| `$0 (local)` | local inference, tokens still tracked |
| `~$0.01` + "simulated" | a mock run. Never a real number |

A simulated figure can never masquerade as a measured one, and savings print
`n/a` rather than a number derived from mocks.

**If you see "This total is incomplete"** — some provider has no `pricing:`
entry, so its spend is counted as $0 and is *not* weighed against any budget
ceiling. Add the entry named in the message.

**Cost levers**, each with its own kill switch: phase cache, retrieval-scoped
context, budget envelopes per workflow, and a degradation ladder that drops
non-judgement phases to a cheaper tier at 60% of envelope. Judgement phases
(plan, adversary, generate) never downgrade — they run full quality or the run
aborts.

---

## 8. Switch LLM provider

**You want:** a local model, or a different vendor.

Settings → **LLM provider**, or:

```bash
AIQE_LLM_PROVIDER=ollama make run-pr REPO=orders-api PR=201
```

| provider | class | serves |
|---|---|---|
| claude | agentic | all 10 phases |
| codex | agentic | all 10 phases (ships pre-mapped) |
| ollama | completion | 8 phases, once you map models you have pulled |
| openhands | completion | 8 phases, opt-in (`AIQE_OPENHANDS_PROVIDER=1`) |

**`generate` and `validate` need an agentic provider** — they edit files in the
workspace and run tests. Assigning a completion provider is refused at *config*
time with the fix named, never mid-run.

**There is no silent fallback.** An unreachable provider fails the phase and
says so; it never quietly reroutes to a paid one.

Mixed estates work — cheap local model for triage/analyze, claude for judgement:

```yaml
llm:
  phase_providers: {triage: ollama, analyze: ollama}
```

`make parity-compare` puts providers side by side on commit rate, critic score
and cost. **Currently blocked** on real-LLM auth, so it honestly prints "No
MEASURED parity runs yet" rather than a comforting table.

---

## 9. Traceability for an audit

```bash
make trace-matrix                # ticket → scenario → spec → gate commit → CI
make trace-matrix KEY=PROJ-301 CSV=1
```

One row per scenario, joined on the scenario id stamped into every generated
test. **An approved scenario with no test still gets a row** — that is the line
an auditor should see.

For a point-in-time record:

```bash
make report FORMAT=pdf DAYS=30
make export-plan KEY=PROJ-301 FORMAT=pdf
make attach-plan KEY=PROJ-301          # attach to the ticket
```

---

## 10. Move your estate

**You are:** setting up a second environment, or handing over to another team.

```bash
make state-export                        # -> reports/exports/<stamp>-state.tar.gz
make state-inspect BUNDLE=<path>         # verify checksums, no writes
make state-import BUNDLE=<path> DRY=1    # rehearse
make state-import BUNDLE=<path>          # merge (nothing local destroyed)
```

The bundle carries **work**: registry, org config, guidance, authored per-repo
facts, catalog, run history, plans, specs, testplans, testdata. Every file has a
sha256 and import verifies it.

It **never** contains `.env`, `aiqe.properties`, or code — a bundle gets emailed
and attached to tickets, so credentials must not be in one. Derived data
(caches, vectors, generated guidance) is excluded because it rebuilds.

Import refuses to run while a pipeline holds the lock: rewriting state under a
live run is how you get a half-imported estate.

---

## 11. Diagnose a run

**Start here:**

```bash
make status                              # every run and its gate outcome
python3 bin/qa.py artifacts <KEY>        # generated code, diffs, per-phase spend
ls reports/<KEY>-<test_repo>.log         # the gate's own log
```

**Exit codes tell you where it stopped:**

| code | meaning | what to do |
|---|---|---|
| 2 | scope violation | a run tried to write outside the allowed paths |
| 3 | secret detected | a generated file contained a credential pattern |
| 4 | unmapped test | a new spec has no catalog sidecar |
| 5 | tests failed | the generated tests did not pass — read the log |
| 6 | not a standalone repo | the checkout is wrong; never bypass this |
| 7 | push failed | auth, branch protection, or network |
| 8 | spec unsatisfied | an approved scenario is uncovered and unwaived |
| 64 | invalid key | the KEY has characters that are not path-safe |
| 65 | needs clarification | the ticket was ambiguous — answer and re-run |
| 75 | pipeline busy / unwritable | `PIPELINE_BUSY` = another run holds the lock; `PIPELINE_UNWRITABLE` = the scratch dir is not writable (a volume or permissions problem, NOT contention) |
| 77 | over budget | cost or wall-clock ceiling hit before the gate |

**A run that seems stuck** is usually waiting on the run lock. A killed run
leaves `out/.pipeline.lock` for up to 90 minutes; that is deliberate, so a live
run is never broken by a competitor.

**Check your integrations before blaming the platform:**

```bash
make check-integrations                  # read-only; posts, pushes and sends nothing
```

---

## 12. Answer "who did this, and what happened because of it?"

Every state-changing request and every pipeline transaction is recorded in one
append-only log with an actor, a target, an outcome and a run id — so questions
that used to mean reading four files are one filter.

```bash
bin/qa.py events --run 1785612364-10233
```

That is the whole story of one run: what queued it, which phases ran, what the
gate decided per repo, and what was notified. Other angles:

```bash
bin/qa.py events --outcome refused          # everything the platform said no to
bin/qa.py events --actor anand --kind plan.approved
bin/qa.py events --target PROJ-301          # one ticket, end to end
```

The dashboard's **Activity** view is the same data with filters and a CSV
export for auditors.

**What is deliberately NOT recorded:** GET requests (browsing is not a
transaction), request bodies, and any secret value. A Settings change records
*which keys* changed, never what they were set to.

**If the log could not be written**, the view and the CLI say so — the list is
labelled INCOMPLETE rather than presented as a full history. A partial audit
trail that looks complete is worse than an obviously broken one.

## 13. Get told when something goes wrong, without being spammed

Define rules over that log in the dashboard's **Alerts** view: a kind, an
optional outcome/target filter, a threshold, a window, a channel. They are
evaluated on the nightly `make maintain` tick.

```bash
bin/qa.py alerts            # every rule and its current state
```

Behaviour worth knowing before you rely on it:

* **Firing is a state.** A rule resolves when the condition clears, so the same
  problem recurring alerts you again.
* **A cooldown stops flapping.** A condition that keeps crossing the threshold
  sends one message, not one per tick.
* **A rule that cannot be evaluated says `unevaluable`** and names why. It is
  never reported as healthy — silence from a broken evaluator would otherwise
  look exactly like a healthy estate.
* **Test sends for real.** The Test button uses the actual channel and does not
  retry, because the failure you are testing for is a misconfigured channel.
* **Digest mode** collapses a tick's firings into one message per channel.

Every delivery attempt is recorded, so `notify.failed` tells you the difference
between "nothing happened" and "we could not reach you".

## 14. Adopt the spec-driven workflow without breaking anyone's build

**You want** the team to write requirements before tests, and coverage to be
provable — but not at the cost of a gate that starts refusing commits on Monday.

Open **Spec workflow**. The header states, in plain words, whether anything is
enforced in your estate. Out of the box the answer is no: every step is advisory
and the platform will not stop a run that skips one. That is deliberate — the
process is visible before it is mandatory.

```bash
make requirements KEY=PROJ-301
```

This formalizes the ticket into EARS statements and stops for validation. If the
ticket does not say what should happen, planning halts with exit 65 and a
question on the ticket rather than a guess — the cheapest artifact to change is a
sentence, not a committed test.

Approve them (`make requirements-approve KEY=PROJ-301`, or the Requirements
card), then plan and approve as usual. Approval **signs** the spec; editing an
approved plan revokes the approval.

When the signal looks clean, turn enforcement on in Settings — **`warn` first**.
`warn` reports uncovered scenarios and still commits; `strict` makes the gate
refuse with exit 8. Turning on `strict` first just teaches people to bypass the
gate. Anything genuinely shipping uncovered needs a waiver with a reason, an
owner and an expiry — capped at 90 days, so "temporarily" cannot become
"forever".

`GET /api/governance?format=md` downloads the whole thing as one document,
generated from the constitution, for people who will never open the dashboard.

## 15. Find out how much authoring a spec makes unnecessary

**You want** to know whether the spec-driven route is actually cheaper.

```bash
make spec-savings
```

On a fresh estate every scenario reads uncovered, because nothing has been
generated yet. After a run that commits tests (`make demo-jira`, or a real one):

```
scenarios 3  already covered 1  would author 2
  saving: 1 authoring call(s) avoided — value NOT MEASURED (no measured
  authoring cost on this estate — every run here is simulated. Run
  `make parity-jira` (needs Claude CLI auth) to produce a measured baseline.)
```

Read what that says and what it does not. **One authoring call avoided** is a
measured fact: an approved scenario already exercised by a cataloged test, joined
through the `scenario_id` stamped on every generated test. **The value is
absent** because pricing it needs a measured per-scenario cost, and every run on
this estate is simulated — a zero would read as "no saving" and an estimate would
be read as a measurement.

It is advisory: nothing skips authoring automatically. A wrong join would
silently drop coverage, which is the one failure this platform cannot see, so the
join gets proven against real runs before it is allowed to remove work.

Same numbers in the UI under **Spec workflow → Work this spec makes
unnecessary**.

## 16. Where is my request, right now?

`make status` tells you about runs that finished. This tells you which step a
submitted request is *on*, and when it failed, which step and why.

```bash
python3 engine/lib/run_progress.py <KEY>       # or the Run progress view in the UI
```

```
PROJ-301  source=record  overall=committed
  [done    ] Route the request          e2e-api-tests-1
  [done    ] Author the test plan       3 scenario(s)
  [done    ] Write the tests            1 created, 0 updated
  [done    ] Quality gate               e2e-api-tests-1: committed
```

**Read the states literally.** `pending`, `running`, `done`, `failed` and
`skipped` mean what they say. **`unknown` means the step could not be observed** —
it is not a synonym for "not yet". A run whose lock has gone stale (over 90
minutes, matching the pipeline's own threshold) reports its current step
`unknown` and `busy: false`, so a dead run stops your polling instead of
spinning forever. A record with no gate block does **not** report the gate as
passed: a run that aborted at exit 77 never reached it.

A failing step carries the exit code's documented meaning, the log path and the
log tail. Nothing here is new instrumentation — every signal already existed;
this reads them.

## 17. Why did the AI do that?

Before you act on generated tests, you can ask what the model was actually
working from.

```bash
make explain KEY=PROJ-301                      # or the "Why the AI did this" panel
```

It answers, from recorded evidence only: which test repos were chosen and which
rule fired; **what each phase was shown and what was withheld from it**; which
model wrote each phase and whether a budget rung downgraded it; what the
read-only adversary found and the arbiter accepted; whether the plan was fresh
or adapted; and the gate's verdict with the exit code's meaning.

The withheld list is the one people underuse. A dropped context chunk is
knowledge the model did not have, which explains an omission that nothing in the
output can:

```
What was the model shown for the `triage` phase?
  -> 24 chunk(s) kept, 8 dropped
     - WITHHELD to fit the budget: catalog:web-storefront-ui:mapped, …
```

**A decision whose reason was not recorded comes back under `unexplained`,
naming what is missing.** It will not invent a rationale — a fabricated one is
confidently wrong about exactly the thing you came to check, and is
indistinguishable from a real one. Context manifests live in `out/` and the next
run overwrites them, so for a historical run this reports them *unavailable*
rather than as "nothing was dropped".

## 18. Approve some of it, not all of it

A reviewer who likes nine scenarios out of ten should not have to accept the
tenth or reject the batch.

```bash
make select KEY=PROJ-301                                  # what is included now
python3 engine/lib/selection.py PROJ-301 --exclude-scenario PROJ-301-S2
make select-finalize KEY=PROJ-301                         # emit the approved artifact
```

```
PROJ-301: 3 scenario(s), 1 test(s)
  [x] PROJ-301-S1  boundary rejection
  [x] PROJ-301-S2  stacking on an already-discounted order
  [x] suites/orders/PROJ-301-discount-boundary.spec.js  (already committed)
```

Three behaviours are worth knowing before you rely on it:

- **An item nobody ruled on is included.** Not deciding is not rejecting, and
  defaulting the other way would finalize an untouched plan to nothing.
- **Excluding a test the gate already pushed cannot un-push it.** Those are
  reported `already_committed`, with the follow-up named and collected under
  `needs_follow_up`. A reviewer believing a test is gone while it runs in CI that
  night is the worst thing this product could tell you.
- **Finalizing with everything excluded is refused**, because an empty approved
  plan reads downstream as "this ticket needs no tests".

`finalize` writes `reports/approved/<KEY>/` — the plan re-rendered from the kept
scenarios only, plus a manifest of who approved what. The authored spec under
`specs/<KEY>` is never rewritten: it stays the record of what was *proposed*, so
"what did the reviewer turn down?" remains answerable.

## What this platform will not do

Worth knowing up front, because each is a deliberate design decision:

* **It will not commit from an LLM phase.** Only the gate writes to a repo.
* **It will not guess at an ambiguous ticket.** It asks and stops.
* **It will not auto-approve a reused plan.** Reuse always lands as a draft.
* **It will not edit your test repo's CI config**, even to exclude a flaky test.
* **It will not silently switch providers**, even if the one you chose is down.
* **It will not report a simulated cost as a real one.**

If you want the reasoning behind any of these, `specs/platform/constitution.yaml`
is the machine-readable list, and every clause names the test that enforces it.
