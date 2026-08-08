# User Guide

> This is the **reference manual** — concepts, configuration, every command
> and its options. If you are trying to get a specific job done, start with
> `docs/use-cases.md`, which is organised by task and links back here for
> the detail.

Operating, configuring, and integrating the AI QE Platform. For first-run setup see
[Getting Started](getting-started.md); for design rationale see
[architecture.md](architecture.md) (section numbers below refer to it).

---

## 1. Concepts in one page

- **Workflow A (PR-triggered test sync):** a PR in any registered source repo triggers
  resolve → triage → generate → validate → gate. E2E tests stay in sync with the change.
- **Workflow B (JIRA-triggered authoring):** a labeled ticket triggers analyze →
  testplan → testdata → generate → validate → gate. Ticket → plan + data + passing tests.
- **Registry** (`registry/repo-registry.yaml`): declarative source of truth for routing —
  which source repos exist, which test repos cover them, JIRA component/label hints.
- **Catalog** (`catalog/*.jsonl`): every test mapped to app repos with evidence and a
  confidence score. The registry's `covers:` map is *generated* from it, never hand-edited.
- **Gate** (`engine/gate/gate.sh`): deterministic script, the only place a commit/push
  happens. Everything an LLM produced must pass through it.
- **Ports & adapters:** the engine calls six vendor-free ports (Scm, Tracker, Knowledge,
  Cicd, Notify, Telemetry). Real adapters and mock adapters implement identical verbs.
- **AGENTS.md** (repo root): generated estate knowledge — live endpoints/routes with
  `[NO TEST]` coverage-gap annotations, coverage index, conventions — injected into
  every LLM phase and auto-refreshed by runs, config changes, and mapping edits.
  Never hand-edited.
- **Work queue + TaskEvent receiver:** runs are started by CI webhooks →
  `bin/taskevent_receiver.py` (validated, deduped), the served dashboard (fetch by
  release / pasted JIRA text), or the CLI — all feeding one locked queue drained by
  `make queue-run`.
- **Per-key tracking:** every PR/JIRA key carries team-review status and target
  release; plans export to md/HTML/Word/PDF, mirror to Confluence, and attach to the
  ticket.
- **Mock mode (`AIQE_MOCK=1`):** LLM phases and external tools stubbed; resolver, gate,
  environment provisioning, git mechanics all real. This is what the `demo-*` targets use.

## 2. Running the platform

### Demo estate (no credentials)

```bash
make demo-bootstrap   # catalog bootstrap on demo test repos
make demo-pr          # Workflow A on fixture PR orders-api#201
make demo-jira        # Workflow B on fixture ticket PROJ-301
make discovery-eval   # A4 ticket-discovery fixtures and per-signal metrics
make review           # full regression: goldens + conformance + adversarial gate + eval
```

### Real estate

```bash
cp .env.example .env         # fill in what your estate uses (see §6 below)
make bootstrap REPO=<test-repo>      # once per test repo, then review the queue
make run-pr REPO=<source-repo> PR=<number>
make run-jira KEY=<PROJ-123>
```

### Reading a run

Every run prints a per-test-repo summary and posts it to the trigger surface:

| Status | Meaning |
|---|---|
| `committed ✅` | Gate passed; commit on `test/<KEY>-ai-qe` in that test repo |
| `no changes ➖` | Pipeline decided no test updates were needed there |
| `quarantined ❌ (exit N)` | Gate blocked the commit — see `reports/<KEY>-<repo>.log` and the exit-code table below |

Partial success is intentional (§5.8.5): a failure in one test repo never blocks a good
commit in another.

### Gate exit-code protocol

| Exit | Marker | Meaning |
|---|---|---|
| 0 | `GATE_STATUS=COMMITTED <sha>` / `GATE_STATUS=NO_CHANGES` | Success |
| 2 | `SCOPE_VIOLATION` | Agent wrote outside `tests/ suites/ fixtures/ data/ pages/ catalog/ .ai-qe/` |
| 3 | `SECRET_PATTERN` | Credential-looking string in new content |
| 4 | `UNMAPPED_TEST` | New spec without a catalog sidecar entry (born-mapped rule) |
| 5 | `TESTS_FAILED` | Generated specs failed against the provisioned environment |
| 6 | `GATE_REFUSED` | Working directory is not a standalone test repo (safety backstop) |
| 7 | `PUSH_FAILED` | Commit succeeded but the push to the configured remote failed |

Environment-provisioning failures (`with-env.sh`'s own exits 7/8) surface through the
gate as **exit 5** with `APP_START_FAILED` / `APP_REPO_NOT_FOUND` in the run log.
Separately, **pipeline exit 77 = `BUDGET_EXCEEDED`** (cost or wall-clock ceiling hit
before a phase) — the run aborts and notifies before any gate runs, so no gate exit
code is emitted at all.

Non-zero exits quarantine the run for human inspection; they are never auto-retried.
The adversarial suite (`make test-gate`) permanently regression-tests codes 2–5.

### 2a. The dashboard, view by view

`make serve` opens fifteen views. **[ui-guide.md](ui-guide.md)** documents each
one: what it answers, what you can do in it, and what it deliberately refuses to
tell you (an unmeasured cost is never rendered as a measured one, and a view
that could not load says so rather than showing an empty table).

Start there if you are new. The two views most people miss are **Spec workflow**
— where each ticket is in the six-state process and what is blocking it — and
**Activity**, the transaction log of who did what.

In Spec workflow, a waiver needs the scenario id, a durable reason, an owner,
and an expiry. Enter the owner explicitly in local/token mode; behind trusted
SSO the configured identity header overrides the typed owner. Add/remove and
requirements approval are authenticated JSON mutations, while simply loading
the board, requirements, or waivers is read-only.

## 3. Configuration reference

Three layers, each overriding the previous (§5.10): platform defaults → org config →
per-repo config.

### Configuration sources (`aiqe.properties`, `.env`, environment)

Every variable the Settings page exposes can come from a Java-style properties file
instead of `.env` — useful when JIRA/Stash credentials are already managed as
`.properties` by Ansible, a config repo, or an OpenShift ConfigMap.

```properties
# aiqe.properties  (copy from aiqe.properties.example)
SCM_KIND = stash
STASH_URL https://stash.company.com
JIRA_URL: https://jira.company.com
ATLASSIAN_MCP_TOKEN = ...
```

Precedence, **lowest to highest**:

    aiqe.properties  <  .env  <  explicit environment

`.env` wins over the properties file on purpose: `.env` is what the Settings page
writes, so if properties outranked it, saving in the UI would appear to do nothing.
Treat properties as the baseline you deploy with, `.env` as the local override, and an
exported variable as the final word.

Discovery: `$AIQE_PROPERTIES` (a path, or a comma-separated list — first that exists
wins), else `./aiqe.properties`, else `./config/aiqe.properties`. Loaded at startup by
both `engine/pipeline.sh` and every Python entry point via
`settings_store.load_env_into()`.

```bash
make config     # which file is loaded and which keys it sets (names only, never values)
```

The Settings view tags any field supplied by the properties file with a **properties**
chip, so a value you cannot find in `.env` is still traceable. The real
`aiqe.properties` is gitignored — it holds API tokens; only the example is committed.

### `registry/org-config.yaml` (org layer)

```yaml
models:            # model tier per phase; escalate after 2 failed generate attempts
phases:            # per-phase max_turns + allowedTools whitelist (least privilege)
openhands:         # how much the estate depends on OpenHands (it is OPTIONAL)
  mode: auto                # off | auto (hybrid, default) | required
                            # auto = use it when reachable, fall back to CI/receiver
                            # when not; an outage is `degraded`, never fatal.
                            # AIQE_OPENHANDS overrides for a single run.
critic:            # advisory test-quality score; NEVER gates a commit (§5.8.7)
  enabled: true             # AIQE_CRITIC=0 skips the phase for a single run
  accept_threshold: 0.8     # >= this -> "accept";  >= review_threshold -> "review"
  review_threshold: 0.5     # below it -> "weak" (still commits — the gate decides)
plan_adversary:    # adversarial test-plan review, BEFORE the human approval gate
  enabled: true             # AIQE_PLAN_ADVERSARY=0 skips it for a single run
                            # A read-only adversary hunts for what the plan author
                            # missed (negative / boundary / authz / state /
                            # cross-repo / data) and an arbiter folds the accepted
                            # gaps in as extra scenarios. It only ever ADDS, and if
                            # either phase fails the authored plan stands unchanged.
generate_fanout:   # one generate agent per resolved test repo
  enabled: true             # AIQE_GENERATE_FANOUT=0 forces the single-agent path
                            # With >=2 test repos each agent sees ONLY its own repo's
                            # conventions and may write only to that repo, so a
                            # contract change fanning out to an API repo plus consumer
                            # UI repos cannot cross-wire their approaches. A single
                            # resolved repo takes the original single-call path.
review:            # generated-test semantic reviewer after validate
  enabled: false            # AIQE_TEST_REVIEWER=1 enables it for one run
  agent_gate: warn          # off=no review; warn=surface+gate; require=pre-gate refusal
  on_unavailable: proceed   # under require: proceed or hold before the gate
  max_loops: 1              # bounded reviewer repair/revalidate/rereview loops
  reviewers: []             # optional human review assignment rota
resolution:
  confidence_threshold: 0.8   # below this the pipeline asks a human instead of guessing
catalog:
  auto_accept_confidence: 0.85
  review_band: [0.5, 0.85]    # between these → human review queue; below → orphan
budgets:           # per-run cost ceilings — ENFORCED: the pipeline checks cost
                   # + wall-clock BEFORE every phase; over-limit runs abort with
                   # exit 77 and a notification, before the gate. Precedence:
                   # MAX_COST_USD_PER_RUN (.env / Settings) beats these; cross_repo
                   # applies when a run targets >1 test repo. Mock runs meter
                   # nothing (only wall-clock applies); real claude phases meter
                   # their reported total_cost_usd into out/cost.tsv and the run
                   # record's cost_usd.
adapters:          # which adapter script serves each port
```

Roll out agent review in two steps: enable and measure it under `warn`, then
change the estate-wide `review.agent_gate` to `require` only after clean controls
and false-refusal rates are acceptable. Final `needs_work` under `require` exits
78 before the deterministic gate and names the fixes on the run record and
PR/ticket comment. There is no per-run consequence bypass; changing behavior
requires an audited org-config edit. `off` always suppresses the reviewer, while
`require` cannot be neutralized with `AIQE_TEST_REVIEWER=0`.

### `registry/repo-registry.yaml` (routing)

- `source_repositories[]` — `type`, `scm`, `domains`, `testable_paths` (changes outside
  these skip the pipeline), `contract` (OpenAPI file; changes fan out to consumers via
  `consumed_by`), `route_table` (frontend).
- `test_repositories[]` — `layer` (api|ui), `framework`, `layout`, `covers` (generated).
- `routing_hints` — `jira_component_map` (Component → source repos) and
  `jira_label_map` (e.g. `api-only` → restrict to API-layer test repos).

Registry changes go through PR review and are pinned by golden tests
(`registry/tests/`) — run `make test-routing` after any edit.

### `.ai-qe/config.yaml` (per test repo)

```yaml
framework: playwright        # informational; commands below are what the gate runs
commands:
  lint: npm run lint         # demo estate uses "true" (no linter) and "node --test"
  test: npx playwright test
test_env:                    # consumed by bin/with-env.sh (G5)
  mode: compose              # compose = hermetic app-under-test per run
  app_repo: orders-api       #   resolved from workspace/src/ first, then demo/
  app_entry: docker-compose.yaml   # demo estate: app/server.js
  base_url_env: BASE_URL     # exported to the test process
  # mode: shared             # alternative: point at a standing QA environment
  # url: https://qa.example.com
```

The gate is framework-agnostic by construction: it runs whatever `commands.lint` /
`commands.test` say, inside the environment `test_env` describes, with guaranteed
teardown.

### `CLAUDE.md` in each repo (behavior policy)

`templates/test-repo/CLAUDE.md` and `templates/source-repo/CLAUDE.md` are the drop-in
policies (selector strategy, JIRA-key tagging, born-mapped rule, "never guess on
ambiguous ACs"). They are versioned and reviewed like code.

## 4. The Test Catalog lifecycle

1. **Bootstrap** (`make bootstrap REPO=...`): extract (static analysis) → correlate
   (endpoints ↔ OpenAPI contracts, routes ↔ route tables, JIRA keys ← git history) →
   LLM-classify only the unresolved residue → tier:
   confidence ≥ 0.85 `auto` · 0.5–0.85 review queue (`catalog/review/*.csv`) · below `orphan`.
2. **Review**: QE confirms/edits the queue; orphans are deprecation candidates.
3. **Born-mapped forever after**: every generated spec ships its catalog entry in the
   same commit (`catalog/generated.jsonl` sidecar); the gate enforces it (exit 4).
4. **Coverage regeneration**: `catalog/bootstrap/regen_coverage.py` rewrites the
   registry's `covers:` from the catalog after every bootstrap.

Query it with `jq`: e.g. *which tests cover orders-api?* —
`jq -c 'select(.mapping.app_repos | index("orders-api"))' catalog/*.jsonl`

## 5. Monitoring, tracking & mapping management (QA operations)

Everything below reads three data sources — persistent run records
(`reports/runs/*.json`), the test-knowledge catalog (`catalog/*.jsonl`), and the
registry — through one CLI (`bin/qa.py`) and one dashboard.

### Monitoring runs

```bash
make status            # recent pipeline runs: trigger, overall, per-repo gate outcome
make dashboard         # regenerate reports/dashboard.html (open in any browser)
```

`make status` output — one line per run, commit SHAs for committed repos, exit codes
for quarantined ones (log path: `reports/<KEY>-<repo>.log`):

```
run_id             trigger                overall      gates
1784594232-32186   jira:PROJ-301          OK committed e2e-api-tests-1=committed@f78af97, e2e-ui-tests-1=no_changes
```

Every pipeline run persists a structured record to `reports/runs/<RUN_ID>.json`
(trigger, per-phase contracts, per-repo gate status/exit/commit) — the same record is
emitted through the Telemetry port to Splunk. Run history is committable, so the QA
team can track it in git or scrape it into any BI tool.

**The dashboard** (`reports/dashboard.html`, self-contained, light/dark aware) shows:
KPI tiles (runs, quarantines, catalog health, uncovered repos), the recent-runs table,
the app-repo × test-repo coverage matrix, and the full catalog with client-side
repo/status/text filtering. Regenerate any time; it needs no server.

### Viewing generated artifacts (test plans & E2E tests per PR / story)

```bash
python3 bin/qa.py artifacts PROJ-301           # what did the latest run generate?
python3 bin/qa.py artifacts orders-api-201     # PR keys work with or without PR- prefix
python3 bin/qa.py artifacts PROJ-301 --full    # print the plan AND the generated test code
python3 bin/qa.py artifacts PROJ-301 --all     # every recorded run for the key
```

One view per key: the test plan (`testplans/<KEY>.md`) and its scenario table, canonical
test data (`testdata/<KEY>/`), the generated spec list with create/update actions, open
questions, validation results (passed/failed/repair loops), and per-repo commits.
Because `workspace/` is ephemeral, every gate commit is archived as a reviewable diff in
`reports/runs/<RUN_ID>-<repo>.diff` — `--full` prints it, so the exact generated test
code is reviewable long after the run (and in real estates, before merging the
`test/<KEY>-ai-qe` branch).

The dashboard has the same view: the **Generated artifacts** section lists the latest
run per key with expandable plan, scenarios, data, and test-code blocks — each
generated spec rendered with syntax highlighting and, for updated or deleted specs, a
**before/after comparison** built from the archived gate diff (new / updated / deleted
chips per file, hunk-level view).

### Running from pasted JIRA context (no ticket needed)

The requirement "pass JIRA context as text input" is served by inline runs — paste the
story/bug/security-fix text and Workflow B runs without an existing ticket:

```bash
python3 bin/qa.py run-inline "Refund bug
Refunds above the order total return 500 instead of 400.
AC-1: refunds above total rejected with 400" \
  --repos orders-api --labels api-only --type Bug
```

The first line becomes the summary; `AC-…` lines become acceptance criteria;
`--components/--labels/--repos` drive routing exactly like a real ticket (give at
least one or the run will ask for clarification, by design). `--queue` enqueues
instead of running. The served dashboard has the same thing: **Run from pasted JIRA
context** (textarea + routing fields) inside *Fetch & queue work*.

### Issue-type-aware generation

Workflow B adapts to the ticket's issue type (from Jira's `issuetype`, the inline
`--type` flag, or a `security` label): **Story/Enhancement** → extend-first bias and
per-AC boundary coverage; **Bug** → a regression test encoding the exact reproduction
path plus surrounding boundaries; **Security** → negative/abuse-case tests that assert
the fix without weaponizing the flaw. The guidance prompts live in
`prompts/issue-types/` and are injected into the analyze/plan/generate phases.

### PR review depth & merge-gate visibility

Workflow A now feeds the triage and generate phases the **actual PR diff** (Scm `diff`
verb: `gh pr diff` on GitHub, the raw diff endpoint on Bitbucket Cloud, flattened
hunks on Stash) — not just the changed-file list. After the gate, the run posts a
**build status** to the PR head commit (Scm `set_status`: success/failure as
`ai-qe`), so quarantined runs are visible in the merge UI, not only in comments.

After the build status, the run also posts a **coverage-delta comment** on the PR
(`engine/lib/pr_comment.py`): behaviors now covered, tests created vs updated,
validation outcome, gate result with a pointer to the `test/<KEY>-ai-qe` branch when
committed, open questions, the agent-review verdict/findings/repair count/policy,
and the advisory critic score + run cost. It stays silent
when triage finds no E2E impact — no noise on refactor-only PRs.

The same report is viewable **after the fact** without the PR: the dashboard's
Artifacts view shows a **PR coverage report** panel for PR keys (rebuilt from the
persisted run record) with a markdown download, and
`GET /api/pr-coverage?key=PR-…` (add `&download=1` for the file) serves it to
scripts.

### Team-review tracking (who has looked at the generated tests?)

Every PR / JIRA key whose run **commits** generated artifacts is automatically marked
`pending_review` ("yet to be reviewed") — including keys that were previously approved,
because a new commit means new artifacts. The team then moves it through the lifecycle:

```bash
make reviews                                          # the review board
python3 bin/qa.py mark PROJ-301 in_review --by anand
python3 bin/qa.py mark PROJ-301 approved  --by anand --note "LGTM - boundary coverage"
python3 bin/qa.py mark PR-orders-api-201 changes_requested --by anand --note "add 404 case"
```

The board shows the latest **agent review** verdict and unresolved count beside
the team status. They are deliberately separate: `approve` or `needs_work` from
the agent is context and can never set the human `approved` or
`changes_requested` state. The Guided run and Run progress views place an
**Agent review** step after validation and before the quality gate. `make
explain KEY=...` lists the recorded findings, repair-loop count, surviving
findings, and the `agent_gate` policy captured for that run. Disabled and
unavailable reviewers are shown explicitly rather than omitted.

When the agent reviewer returns `needs_work`, the pipeline makes at most
`review.max_loops` findings-driven repair passes (one by default). Only affected
test repositories run the named repair phase; it may edit existing generated
specs but cannot create new files or run tests. Each pass is separately metered,
then the normal validate phase reruns and the read-only reviewer examines the
result again. Unfixed or repeated findings remain visible after the cap—even if
the last raw reviewer response says approve. Set `max_loops: 0` to keep review
advisory without automatic repair; disabling the reviewer also disables repair.
The run budget and wall-clock limit apply before every additional phase.

Statuses: `pending_review` → `in_review` → `approved` | `changes_requested`.
State lives in `reports/runs/reviews.json` (committable; full transition history per
key). It surfaces everywhere: `make status` has a *team review* column, the dashboard
shows a chip per run plus an "awaiting team review" KPI tile, and
`bin/qa.py artifacts <KEY>` prints it in the header.

**Release-version tracking** rides on the same store: each key carries the release it
targets. JIRA keys get it **automatically** from the ticket's `fixVersions` (Workflow B
captures it at resolve time; the real Jira adapter and the demo fixture both supply
`fix_versions`). PRs set it manually:

```bash
python3 bin/qa.py release PR-orders-api-201 2026.08
```

The release appears in `make status`, `make reviews`, the dashboard's *release* column
(with a **release filter** above Recent runs — pick a version or "(no release)" to
narrow the table), and the artifact cards — so the team can answer "which release does this generated test
work belong to, and has it been reviewed?" in one view. Status transitions never touch
the release; changing it appends to the key's history with its source (`jira`/`manual`).

### Trace: story/PR → plan → tests → gate → review → release

One chronological view of everything that happened to a key — the traceability chain
engineering managers ask for, exportable evidence included:

```bash
python3 bin/qa.py trace PROJ-301            # timeline on stdout
python3 bin/qa.py trace PR-orders-api-201
```

Events are joined from the plan store (drafted / edited / approved — **who and when**),
the run records (phases, tests created vs updated, validation, per-repo gate outcome,
advisory critic score, cost), and the review board (status transitions, release
assignments). The served dashboard has the same thing as the **Trace** view (pick a
key, `GET /api/trace?key=...`) — with the SSO identity on approval events when
`AIQE_SSO_HEADER` is configured.

### Guided run (wizard): the two long journeys, step by step

The **Guided run** view sequences the two multi-step journeys for someone who
does not yet know which view does what:

- **Pull request → E2E tests** — enter the app repo + PR number, press *Analyze
  PR & generate tests*. The wizard queues the run, drains the queue, and shows a
  live step ladder: generate → quality gate → team review, with the generated
  spec list and a coverage-report download when it lands.
- **JIRA ticket → plan → E2E tests** — enter the ticket key, then *Author test
  plan* → *Approve plan* → *Generate tests* → *Comment plan + tests on the
  ticket*, with the human-approval step rendered **blocked** until a person acts
  (the plan-first invariant, visible rather than implied).

Generation is **asynchronous** — a run takes minutes, an OpenHands conversation
longer — so the wizard polls `GET /api/wizard/status?key=…&mode=pr|jira` while
work is in flight and stops when it isn't. Leave the page and come back: the
ladder always reflects current engine state, because it is *derived* from the
same stores everything else uses (work queue, run records, plan state, review
board) rather than any wizard-private progress record.

Every button drives an **existing** endpoint (`/api/queue`, `/api/plans/status`,
`/api/plans/generate`, `/api/plans/comment`) — the wizard adds sequencing and
visibility, never a second code path. `python3 engine/lib/wizard_status.py <KEY>
[pr|jira]` prints the same status on the CLI.

A visible ladder belongs to exactly one target. Editing any PR or ticket field
clears that ladder and its artifact links immediately; a late status response for
the previous target is discarded, so rejected or rapidly changed submissions
cannot display another key's successful result.

### Interactive dashboard: fetch by release & manual work queue

```bash
make serve        # http://localhost:4999 — the dashboard with live actions
```

The dashboard (implemented from the "QA Dashboard" Claude Design) is a fifteen-view
app whose sidebar is grouped **Start** / **Work** / **Insight** / **Configure**:

| Group | Views |
|---|---|
| Start | **Overview** (KPI tiles, needs-attention feed, coverage matrix, Start-here panel), **Guided run** (paste a PR URL or ticket and follow the ladder) |
| Work | **Intake & queue**, **Run progress** (which step a request is on), **Test plans**, **Runs & reviews** |
| Insight | **Spec workflow**, **Trace**, **Cost**, **Artifacts**, **Activity**, **Alerts** |
| Configure | **Test catalog**, **Repositories**, **Settings** |

Toast feedback and pending-work badges appear on the nav.
[ui-guide.md](ui-guide.md) documents each view — what it answers, and what it
deliberately refuses to tell you.

Served (rather than opened as a file), the **Intake & queue** view becomes active:
type any release/fixVersion (known versions autocomplete; free text works — the
Tracker port's `search_release` verb takes an arbitrary fixVersion), *Fetch items*
lists the JIRA tickets targeting it (JQL in real mode, benchmark fixtures in
mock) plus known PRs whose tracked release matches, and each row has a *Queue*
button — JIRA rows also get **Plan only**, which queues `pipeline.sh plan <KEY>`
so the ticket becomes a draft test plan that stops for human approval instead of
generating tests immediately. The Test plans view has the same two entry points
(*Author plan (queue)* / *Author via OpenHands*) plus the inline card's **Plan
via OpenHands**, which hands a pasted description to an OpenHands conversation
(framed as data, never instructions) that authors the plan via LLM and stops at
the approval gate (`POST /api/openhands/agent`; refused with a hint when
`AIQE_OPENHANDS=off`). *Run queue* drains the queue — items run through `engine/pipeline.sh`
sequentially, statuses (`queued → running → done|failed`) refresh live, and finished
runs appear under Runs & reviews on reload. That view also has release/review filters
and an **Approve** button per pending run (`POST /api/review` — the dashboard
equivalent of `qa.py mark <KEY> approved`). The queue table's *actions* column lets you
**re-queue** a failed item (fresh attempt, previous result cleared) or **remove** any
non-running item (`work_queue.py requeue|remove <id>` from the CLI).

The queue is also scriptable (state in `reports/runs/queue.json`, committable):

```bash
python3 engine/lib/work_queue.py add jira PROJ-301 "" 2026.08 anand
python3 engine/lib/work_queue.py add pr orders-api 201 2026.09
make queue-run          # drain (AIQE_MOCK=1 unless you export otherwise)
```

Duplicate pending items are deduped. The server runs mock adapters by default; export
`AIQE_MOCK=0` (with credentials) before `make serve` for real estates.

Set `AIQE_TICKET_SEARCH=1` before `make serve` to enable structured JIRA intake.
The fetch row then ANDs release, issue type, component, label, status, and text;
shows ticket attributes plus “showing N of M”; and can queue the returned page
after an explicit N-of-M confirmation. Bulk queue still submits each ticket
through the ordinary intake endpoint. Queue attributes are fetch-time display
provenance only—the runner refetches the ticket at start and uses that current
JIRA state for routing and generation. Search failure is displayed in the results
area and is never presented as an empty successful result.

### Repositories & mapping: manage the estate from the UI

The dashboard's **Repositories** view (backed by `engine/lib/repo_admin.py`) manages
the whole estate without touching YAML:

- **Application repositories** — add or edit UI and service repos (kind `ui`/`service`
  maps to the registry's `frontend`/`backend`), SCM (`bitbucket`, `github`, `stash`),
  domains, testable paths, contract / route table, and consumed services (reverse
  `consumed_by` links are maintained automatically). Rows flag repos with no E2E
  coverage.
- **E2E test repositories & mapping** — add or edit test repos, and set each repo's
  **scope**: the app repos it is responsible for (one API test repo covers many
  service repos; one UI test repo covers many UI repos). Scope is the hand-managed
  input; `covers` remains **generated** as catalog evidence ∪ scope
  (`regen_coverage.py`), so routing picks up a new mapping immediately — before any
  test evidence exists — without ever hand-editing coverage.
- Every mutation validates references, re-runs the routing goldens and regenerates
  `AGENTS.md`. Removals are guarded (an app repo still covered, or a test repo with
  cataloged tests, is refused).

CLI parity (same validation path):

```bash
python3 bin/repos.py add-app payments-ui --kind ui --url workspace/payments-ui --scm bitbucket
python3 bin/repos.py add-test e2e-payments --layer api --framework playwright-api --url workspace/e2e-payments
python3 bin/repos.py scope e2e-payments "payments-api, orders-api"
```

### Generated tests follow your repo's existing approach

Convention *text* alone can't stop a model from inventing a new pattern that still
passes the gate. So every generation (and repair) phase also receives an
**existing-approach context** built deterministically from the target test repo's
own code (`engine/lib/spec_exemplars.py` → `out/repo-conventions.md`):

- **shared helpers** — modules imported by two or more existing specs (the repo's
  sanctioned client/util layer), included as full code so the agent *reuses* them
  instead of hand-rolling equivalents;
- **exemplar specs** — the existing specs whose imports best match the repo norm,
  shown as "mirror this shape" (paths under `legacy/`/`deprecated/` are penalized —
  they demonstrate the old approach new tests must not copy either);
- **observed conventions** — test-fn style (`test`/`it`), `require` vs `import`,
  assertion library, file naming.

The generate prompt makes it binding ("no new HTTP clients, wrappers, assertion
helpers, frameworks or layouts"), the repair phase must fix *within* the existing
approach, and the advisory critic flags any `new-approach` deviation for the
reviewer. A brand-new test repo with no specs yet degrades to its
CLAUDE.md/AGENTS.md conventions — there is no approach to follow until one exists.

### Per-repo agent guidance (AGENTS.md / CLAUDE.md)

Two guidance sources steer test generation, test plans and coverage-gap fixes for
each repo, both merged into the estate `AGENTS.md` (injected into every LLM phase):

1. **Team notes** — `knowledge/repos/<repo>.md`, edited from the Repositories view's
   guidance card or `bin/repos.py notes <repo> --set "..."` / `--file f.md` /
   `--clear`. Conventions, selectors, auth flows, data setup.
2. **Repo-local files** — any `AGENTS.md` or `CLAUDE.md` committed inside the app or
   test repo itself. Teams own their guidance in their own repos; the platform ingests
   it on every regeneration.

#### Curated guidance: durable, editable, exportable per-repo AGENTS.md / CLAUDE.md

Generated guidance (`knowledge/generated/`) is rebuildable scratch. When you want
to OWN a repo's guidance from the platform — edit it, keep it across deployments,
export it — curate it in the Repositories view's **Curated guidance file** card:
*Load generated draft* pulls the platform-generated AGENTS.md into the editor,
edit, pick `AGENTS.md` or `CLAUDE.md`, **Save** (persists to
`knowledge/curated/<repo>/`, a **tracked** directory — committed with the control
repo, which is what makes it survive redeployments), **Export** downloads it.
Saving empty content deletes the curated file. API:
`GET/POST /api/repos/curated`, `GET /api/repos/curated/export`.

Ranking: a file the repo itself ships **always wins** (non-negotiable), then the
curated copy, then the demo fixture, then generated scratch — curating never
overrides a team's own committed guidance. "Clear demo data" keeps curated files
(they are user content); only factory reset removes them.

#### Syncing repo guidance from the SCM

Repo-local guidance is normally picked up from the workspace clone made during a run.
To refresh it **on demand** — without waiting for a run — pull it straight from
Bitbucket / GitHub / Stash through the Scm port's `fetch_file` verb (no clone):

```bash
make sync-guidance                    # every app repo (ui + service) AND test repo
make sync-guidance REPO=orders-api    # one repo   (REF=<branch|sha> to pin a revision)
make sync-status                      # when each repo was last synced + what it carries
```

Fetched files are cached under `knowledge/synced/<repo>/` and `AGENTS.md` is
regenerated immediately, so the very next PR triage, JIRA plan, or test-generation run
uses the latest guidance. On the served dashboard, the Repositories view's guidance
card has **Sync all from SCM** and **Sync this repo** buttons, and shows each repo's
last-sync time.

Source precedence is **freshness-based**, not fixed: during a run the workspace clone
(the exact revision under test) wins, while a just-completed sync beats a leftover
clone from an earlier run — so clicking Sync is never silently a no-op. `demo/` is the
last-resort fixture fallback.

The merged section appears as "Repository guidance" in `AGENTS.md` with each
source labeled; `demo/orders-api/CLAUDE.md` is a working example.

#### Structured facts for application repositories

An application repository can opt into machine-readable team assertions by
adding `knowledge/facts/<repo>.yaml`:

```yaml
repo: orders-api
schema: 1
authored:
  conventions:
    - id: public-contract-only
      rule: Test only versioned public endpoints.
      severity: must
```

Run `make repo-facts REPO=orders-api` to rebuild only that repo's gitignored
derived tier, or `make repo-facts` for all E2E repos plus all opted-in app repos.
The application tier contains deterministic registry/dependency facts,
contract/routes, covering suites, and catalog evidence. `status: unavailable`
means the configured contract or route table could not be read; `status:
available` with no items means it was read and contained no recognized surface.

The authored file is tracked and included in state bundles; derived files are
rebuildable scratch. Adding no file changes nothing. Facts are folded through
the same generated-guidance path described above, and never outrank a repo's own
or curated guidance.

### Team status reports

One shareable document answering "what did the AI QE pipeline deliver, what's
waiting on us, and how healthy is the estate" — for standups and release readouts:

```bash
make report                    # all-time report to stdout (markdown)
make report DAYS=7             # rolling window
make report RELEASE=2026.09    # only keys tracked against that fixVersion
make report FORMAT=pdf         # write reports/exports/team-report-<date>.pdf (or html|docx)
python3 bin/qa.py report --days 30 --format docx --out ~/standup.docx
```

Sections: **Summary** (runs, commit rate, tests generated new-vs-extended, avg
repair loops, review backlog, queue backlog), **Completed work** (every committed
run with repo@sha, release and review status), **Quarantined runs**, **Awaiting
team review** (with waiting time), **Work queue**, **By release** rollup,
**Throughput** (runs/day) and **Estate health** (catalog mapping tiers, coverage
gaps, flaky tests from CI ingest). Built by `engine/lib/team_report.py` from the
run records, review board, queue, catalog and CI-health state; HTML/DOCX/PDF
reuse the test-plan exporter's stdlib renderers.

On the served dashboard, the **Overview** view has a *Team report* card — pick a
period and release, then download in any format (`GET /api/report`).

### Email notifications (SMTP)

The platform sends email through an SMTP server configured in the **Settings** view
(or `.env`): `SMTP_HOST`, `SMTP_PORT`, `SMTP_SECURITY` (`starttls`/`ssl`/`none`),
`SMTP_USER`/`SMTP_PASSWORD`, `SMTP_FROM`, and `SMTP_TO` (default recipients). With no
`SMTP_HOST` set — or in mock mode — emails are written to `out/mock-email/*.eml`
instead of being sent, so the whole feature is demoable without a server.

Three things can be emailed (`engine/lib/email_notify.py` builds a plain-text + HTML
MIME message for each):

```bash
make email KIND=report DAYS=7 TO=qa-team@example.com   # the team status report
python3 bin/qa.py email run <RUN_ID> --to lead@example.com   # one run's gate summary
python3 bin/qa.py email digest                         # keys awaiting review (SMTP_TO)
```

On the served dashboard, the Overview **Team report** card has an **Email** button
(`POST /api/email/report`); `/api/email/run` and `/api/email/digest` are also
available.

**As a run notification channel.** Email is a first-class **Notify port** channel
(`adapters/notify/email.sh`). Set `NOTIFY_KIND=email` (or `both` for Slack + email) and
every pipeline run emails its gate summary to `SMTP_TO` — the first line of the summary
becomes the subject. `NOTIFY_KIND` defaults to `slack`, so existing behavior is
unchanged. See [integrations/email.md](integrations/email.md) for provider setup
(Gmail, Office 365, SES, internal relay).

### Settings: configure integrations from the UI

The served dashboard's **Settings** view edits the gitignored `.env` — the same file
every real adapter reads — so JIRA, Confluence, GitHub / Bitbucket Cloud / Stash,
OpenHands, Jenkins, Slack/Splunk, budgets, adapter mode (`AIQE_MOCK`) and SCM kind
can all be configured without a text editor (`GET`/`POST /api/settings`, backed by
`engine/lib/settings_store.py`). Secrets are **write-only**: the UI shows only
whether a token is set (`•••••• set`), never its value — type a new value to replace
it, leave the field blank to keep it. Values save on *Save settings*; adapter-mode
or SCM changes apply to the next pipeline run (restart `make serve` to switch the
server's own fetch source). The editable keys are exactly those documented in
`.env.example` (conformance-tested).

#### Validating the connections

Configuring credentials and *proving they work* are different things. **Validate
connections** in the Settings view (or `make check-integrations`) probes every
configured external system and reports one line each:

| Result | Meaning |
|---|---|
| **connected** | reached it and the credentials were accepted |
| **failed** | configured but unreachable or rejected — the row carries a fix hint |
| **not configured** | nothing set (most estates use a subset — not an error) |

Every check is **read-only**: nothing is posted, pushed, attached or sent. SMTP is
verified by connecting and authenticating without sending a message; the SCM by a
read-only file fetch; JIRA by reading `AIQE_SMOKE_TICKET`. A Slack webhook can only be
truly verified by posting — which would notify the channel — so it is checked for URL
shape and host reachability and says so. Credential values are never echoed back.

```bash
make check-integrations              # all systems
make check-integrations WHICH=smtp   # just one (llm scm jira confluence openhands
                                     #   jenkins slack smtp splunk)
```

The checks probe the **real** systems even while `AIQE_MOCK=1`, so you can confirm a
setup before switching to real mode. Exit code is non-zero if any configured system
failed, which makes it usable as a deployment smoke check. For the deeper,
OpenHands-specific staged test — including an opt-in live conversation that costs
money — use `make smoke-openhands`.

The view's **danger zone** has *Clear demo data* (`POST /api/demo/clear`, or
`make clear-demo` / `DRY=1` from the CLI — `engine/lib/demo_data.py`): it deletes
everything the pipeline *generated* — run history + archived diffs,
review/queue/webhook state, test plans, test data, exports, logs, `out/`,
`workspace/`, the bootstrapped test catalog (JSONL + review queues; the committed
sample and schema stay), the SQLite index, CI-health ingest, and generated/synced
repo guidance — while keeping what the estate *is* (repo registry with your
configured repos, catalog bootstrap code, demo repos, prompts). Because `covers:`
and `AGENTS.md` are *generated* from that now-deleted evidence, both are
**regenerated as part of the clear** — the Repositories & mapping view drops to
scope-only coverage immediately instead of showing stale mappings. It refuses to
run while a pipeline holds the run lock. Rebuild demo state afterwards with
`make demo-bootstrap && make demo-pr`.

Below it sits **Factory reset** (`--factory` on the CLI, double-confirmed in the UI):
everything a plain clear deletes **plus** everything a plain clear deliberately keeps
— the repository registry is emptied (all app and E2E repos removed), team notes
under `knowledge/repos/` are cleared, and `AGENTS.md` + path skills are regenerated
against the now-empty estate. Use it to hand the platform over with no residue;
re-add repos via the Repositories view or `bin/onboard.sh` afterwards.

### Starting a PR run from its URL

The **Guided run** wizard's first field accepts either a registered repo name *or* the
full pull-request URL — Stash (`.../projects/ENG/repos/orders-api/pull-requests/42`),
Bitbucket Cloud (`.../workspace/repo/pull-requests/42`) or GitHub
(`.../owner/repo/pull/42`). Paste the URL and the repo slug, PR number and (on Stash)
the project key are read from it; the PR # box becomes optional.

This matters most on **Stash**, where a repository is addressed by *project key +
slug*. The project comes from the repo's registry entry — its `url` in `PROJECT/slug`
form, or an explicit **Stash project** field in the Repositories view — with
`STASH_PROJECT` as a fallback default only. A repo registered without either fails the
run with `NO_STASH_PROJECT`. Pasting the PR URL sidesteps the guesswork: if the repo
is not registered yet, the queue refuses up front and names the exact values to enter
(`name`, `scm`, `url PROJECT/slug`, `Stash project`) instead of failing later.

**When a queued run fails, the wizard now says why.** The queue records the reason —
an adapter's message (`NO_STASH_PROJECT …`), a documented exit code (budget exceeded,
pipeline lock held, a gate rejection and which check), or the last line of output — and
the failing step shows it. Previously it only said "run failed — re-queue it", which
told you to repeat the thing that had just failed without saying what to change.

**Your credentials survive a factory reset.** It never reads or writes `.env`, so
Stash/JIRA/OpenHands tokens are untouched. What it does remove is the *repositories*,
and the SCM connection check needs one to probe against — so immediately after a reset
**Validate connections** reports the SCM as `credentials configured; no source
repositories registered to probe against` rather than a full green. That is the honest
state: the setup is intact, it just cannot be end-to-end verified until a repo exists.
Add one (or set `AIQE_SMOKE_REPO`) and re-run the check for a real probe.

On the lock: a run that was killed or crashed leaves `out/.pipeline.lock` behind, so
refusing on its mere presence made the button fail forever with a message that wasn't
true. It now matches `pipeline.sh` — a lock older than 90 minutes is treated as dead
and broken automatically. A *fresh* lock still blocks the clear, but the UI offers to
force past it (CLI: `python3 engine/lib/demo_data.py --force`) for when you know the
run is gone.

### Test plans from JIRA: review → edit → approve → link → generate

By default Workflow B runs plan → data → generate → gate in one pass. When a team
wants to **sign off the test plan before any test code is written**, use the
plan-first workflow — the pipeline stops after authoring the plan and will not
generate tests until a human approves it.

```bash
make plan KEY=PROJ-301            # 1. read the ticket, author the plan, then STOP
                                  #    (comments on the ticket + notifies; no test code)
make plan-show KEY=PROJ-301       # 2. review it (or open the dashboard Test plans view)
make plan-edit KEY=PROJ-301 FILE=edited.md BY=you     # 3. edit
make plan-approve KEY=PROJ-301 BY=you                 # 4. approve
make plan-link KEY=PROJ-301       # 5. attach the approved plan to the JIRA ticket
make plan-tests KEY=PROJ-301      # 6. generate E2E tests from the APPROVED plan
                                  #    (normal gate: lint, run, born-mapped, commit)
make plans                        # every plan + status, linked, generating run
```

The same signed lifecycle can start from a pull request when the default-off
S5 flag is enabled:

```bash
AIQE_PR_PLAN=1 AIQE_PR_TICKET_CONTEXT=1 \
  bash engine/pipeline.sh plan orders-api 201
make plan-show KEY=PR-orders-api-201
make plan-approve KEY=PR-orders-api-201 BY=you
AIQE_PR_PLAN=1 make plan-tests KEY=PR-orders-api-201
```

The PR diff is authoritative and a validated discovered ticket enriches it. The
draft link is always posted on the PR and also on that ticket when present. PR
plans are explicitly exempt from the requirements gate because there is no PR
requirements-authoring mode; an approved structured PR plan is still enforced
by the spec gate exactly like a signed ticket plan. Guided run offers the same
Plan first → Approve → Generate ladder without changing normal PR intake.

Lifecycle: `draft → in_review → approved` (or `changes_requested`), tracked in
`reports/plans/state.json` with an append-only history of who did what.

**The plan is challenged before you see it.** Step 1 does not just author the plan: a
read-only *adversary* phase then hunts for what the author missed — negative paths,
boundaries, authorization on mutating behaviors, state/sequencing, cross-repo
consequences, vague data needs — and an *arbiter* judges each finding and folds the
accepted ones in as extra scenarios. The rewritten plan carries an **Adversarial
review** section saying how many gaps were raised, how many were accepted, and why any
were rejected, and the summary line appears on the ticket comment, the Test plans view
and the Guided run wizard.

It can only add: the arbiter is forbidden from dropping the author's scenarios, and if
either phase fails the authored plan stands unchanged and the run continues. It also
happens *before* the approval gate, so it changes what you are asked to approve, never
whether you are asked. Set `AIQE_PLAN_ADVERSARY=0` (or `plan_adversary.enabled: false`)
to skip it.

Two safety properties are enforced, not merely documented:

- **Generation is gated.** `make plan-tests` (and the dashboard button) refuse unless
  the plan is `approved` — the check runs *before* any clone or LLM call.
- **Edits revoke approval.** Editing an approved plan resets it to `draft`, so a
  changed plan can never inherit a stale sign-off. Re-approve to proceed.
- **Concurrent reviews fail closed.** The served editor sends the revision of
  both the plan text and its lifecycle decision. If another reviewer saves or
  changes status first, the stale action is refused with a reload instruction;
  it cannot silently overwrite the newer edit or approval decision.

The generation step feeds the **reviewed markdown** to the phases alongside the
snapshotted plan contract, so reviewer edits actually shape the generated tests.
Tests are produced by the same framework and gate as every other run — nothing about
the existing approach changes.

On the served dashboard, the **Test plans** view lists every plan with its status and
opens an editor with *Save edits*, *Mark in review*, *Request changes*, *Approve*,
*Link to JIRA*, and *Generate tests* (which queues a `tests` run). Demo it end to end
with mock adapters via `make demo-plan` / `make demo-plan-tests`.

#### Linking the plan and the generated tests to the ticket

Two complementary actions in the plan editor (and CLI):

- **Link to JIRA** (`make plan-link`) — exports the approved plan and *attaches*
  the file to the ticket (Tracker `attach`).
- **Comment plan + tests** (`qa.py plan comment <KEY>`, `POST
  /api/plans/comment`) — posts ONE ticket comment linking everything the
  platform produced for the key: the plan (status + approver), the attachment
  ref, the generated E2E tests (files + created/updated), each repo's gate
  outcome with commit SHA and the `test/<KEY>-ai-qe` branch, and the run-record
  id. The durable pointer from the ticket to the delivered tests.

### Exporting a ticket's test plan

Share the generated plan with stakeholders outside Git:

```bash
make export-plan KEY=PROJ-301                 # Markdown -> reports/exports/
make export-plan KEY=PROJ-301 FORMAT=html     # standalone styled HTML (dark-mode aware)
make export-plan KEY=PROJ-301 FORMAT=docx     # Word document (headings, tables, bullets)
make export-plan KEY=PROJ-301 FORMAT=pdf      # PDF (paginated, searchable text)
python3 bin/qa.py export-plan PROJ-301 --format pdf --out ~/PROJ-301-plan.pdf
```

The Word and PDF writers are stdlib-only (the .docx is assembled as the OOXML zip it
really is; the PDF via a minimal native writer) — no extra Python packages needed.

The export bundles the plan (`testplans/<KEY>.md`) with everything reviewers ask for:
target release and team-review status, the scenario table, canonical test data files,
the generated tests with validation results, commit SHAs/branches, and open questions.
On the served dashboard (`make serve`), each artifact card's test-plan header has
**export: md | html | docx | pdf** download links plus a **publish to Confluence**
button (hidden in static-file mode). Unknown keys list the available plans instead of
erroring opaquely.

**Publishing to Confluence** is a one-way mirror (the repo's `testplans/<KEY>.md`
stays the source of truth — the page carries a do-not-edit note):

```bash
make publish-plan KEY=PROJ-301
python3 bin/qa.py publish-plan PROJ-301 --space QA --title "Test Plan - PROJ-301"
```

It goes through the Knowledge port: the mock adapter (default) writes the page to
`out/mock-confluence/`; with `AIQE_MOCK=0` + `CONFLUENCE_URL`/`ATLASSIAN_MCP_TOKEN`
set, the real adapter creates-or-updates the page by (space, title) via the Confluence
REST API and prints the page link. Re-publishing after a new run updates the same page.

**Attaching to the JIRA ticket** uploads the exported plan (PDF by default) as an
issue attachment through the Tracker port:

```bash
make attach-plan KEY=PROJ-301                 # exports the PDF and attaches it
make attach-plan KEY=PROJ-301 FORMAT=docx
python3 bin/qa.py attach-plan PROJ-301 --format docx
```

Every attach path records the resulting reference on the plan state, so the J6 linking
comment names the attachment and the wizard's *Link plan + tests* step shows done — no
matter which one you used. `make plan-link` is the same attach with an approval gate in
front of it (a draft plan is refused); `make attach-plan` attaches whatever exists.

The served dashboard's artifact cards have an **attach to JIRA (pdf)** button next to
*publish to Confluence*. Mock mode drops the file in `out/mock-jira-attachments/`;
real mode POSTs it to `/issue/<KEY>/attachments` with the existing Jira credentials.

### Repository & test knowledge (the catalog as a queryable index)

```bash
make coverage                              # app-repo x test-repo matrix + gap warnings
python3 bin/qa.py tests --app orders-api   # which tests cover this app repo?
python3 bin/qa.py tests --repo e2e-api-tests-1 --status orphan
python3 bin/qa.py tests --layer api        # all API-layer tests across the estate
```

`make coverage` flags two kinds of gaps explicitly: source repos with **no** E2E
coverage anywhere, and test repos whose coverage is empty (bootstrap not yet run).

### AGENTS.md — generated estate knowledge for the LLM phases

`AGENTS.md` (repo root) is the machine-maintained knowledge file injected as context
into every LLM phase (triage, analyze, testplan, testdata, generate). It contains, at
all times: the application-repository table (domains, contracts, consumer graph,
coverage gaps), the **live API surface and UI routes harvested from the actual
contracts/route tables** (freshest clone wins — `workspace/src/` during a run, `demo/`
otherwise), per-test-repo catalog health, the existing-coverage index (the
update-vs-create authority), orphaned tests to avoid extending, JIRA routing hints, and
the generation conventions.

Never edit it by hand — it is regenerated automatically by every pipeline run (right
after cloning, so facts are current), `bin/onboard.sh`, `bin/repos.py` changes,
catalog bootstrap, and `bin/qa.py` mapping edits. Manual refresh: `make agents`.

### Managing app-repo ↔ test-repo mappings

The mapping lives in the catalog; the registry's `covers:` is always regenerated from
it — every command below does that automatically, so routing and mapping can never
drift apart.

```bash
make review-queue                          # what's waiting on a human decision
python3 bin/qa.py apply-review catalog/review/e2e-api-tests-1-queue.csv
python3 bin/qa.py map "<test_id>" --repos orders-api      # confirm one mapping
python3 bin/qa.py map "<test_id>" --repos ORPHAN          # mark dead
```

The review loop end-to-end: bootstrap exports `catalog/review/<repo>-queue.csv` →
QE fills the `decision` column (app repos, or `ORPHAN`) in any spreadsheet tool →
`apply-review` writes the decisions back (status `confirmed`, method gains
`human_review`, unknown repo names are rejected with a pointer to `bin/onboard.sh`) →
coverage regenerates → `make test-routing` still pins routing behavior.

### Quality flywheel (P2)

- **Coverage-gap analysis:** `make gaps` (or `bin/qa.py gaps [--repo R]`) compares each
  app repo's harvested surface (OpenAPI endpoints, frontend routes) against catalog
  evidence and lists what has **no test exercising it**. The pipeline feeds this to the
  triage/generate/plan phases (`out/coverage-gaps.md`), and AGENTS.md annotates
  uncovered surface with **[NO TEST]** — generation targets gaps first. Line-level
  instrumentation remains an estate-specific add-on (`commands.coverage` hook).
- **CI results ingest (Jenkins role 3):** `make ingest-results FILE=<junit.xml>` (also
  accepts a Jenkins `testReport` JSON, e.g. from `adapters/cicd/jenkins.sh
  get_results`) matches cases to catalog tests by title and maintains per-test health
  in `catalog/health.json` — runs, pass rate, last status, and a flaky flag
  (sometimes-passing over ≥3 runs). Health shows in `bin/qa.py tests`, the dashboard's
  *CI health* column, and the scorecard.
- **Scorecard metrics:** `python3 eval/scorecard.py` (also at the end of `make review`)
  now reports routing accuracy, **commit rate**, average **repair loops**,
  **update-vs-create share** (duplicate-prevention proxy), **team acceptance rate**
  (from review decisions), **test health/flakiness**, and reviewer attack quality.
  `make reviewer-eval` is deterministic and always labels its seeded-contract
  catch rates **SIMULATED** (plumbing, not judgement). `make reviewer-eval-real`
  explicitly invokes the configured provider against the same pinned attacks;
  it may incur provider cost and fails with real quality **unmeasured** when the
  same authentication required by parity runs is unavailable.
- **SQLite catalog index:** `make catalog-db` builds `reports/catalog.db` (gitignored;
  JSONL stays the committed source of truth) — rebuilt automatically by bootstrap,
  mapping edits, and results ingest. Ad-hoc queries:
  `bin/qa.py sql "SELECT title, pass_rate FROM tests WHERE flaky=1"` (read-only).
  Tree-sitter-based extraction stays a flagged real-estate upgrade (needs native
  grammar packages the stdlib-only toolchain doesn't ship).

### Team-scale operations (P1)

- **Run isolation & parallel gates:** the pipeline takes an exclusive per-checkout lock
  (`out/.pipeline.lock` — waits up to 2 min, breaks stale locks after 90) because
  `workspace/` and `out/` are shared scratch; parallel capacity comes from one
  sandbox/checkout per run (OpenHands). *Within* a run, per-test-repo gates execute in
  parallel, each booting its own app instance on an OS-assigned free port.
- **Dashboard auth:** set `AIQE_UI_TOKEN` before `make serve` and every request needs
  the token — first browser visit via `/?token=<value>` (sets an HttpOnly cookie),
  API clients via `Authorization: Bearer <value>`. Unset = auth off (localhost dev).
  Behind a reverse proxy, set `AIQE_SSO_HEADER` (e.g. `X-Forwarded-User`) instead:
  requests without the header get **401** (fails closed — a proxy misconfiguration
  never silently opens the dashboard), the header's value becomes the signed-in
  identity shown in the footer, and it **signs approvals and review marks** that don't
  name an explicit actor. The Bearer token keeps working alongside SSO for API
  clients that bypass the proxy. Details: [deployment.md](deployment.md).
- **State-store locking:** `reviews.json` and `queue.json` mutations go through a
  cross-platform advisory lock (`engine/lib/fs_lock.py`), so multiple queue workers,
  the dashboard server, and CLI calls can't corrupt them; queue workers claim items
  atomically.
- **Run-record retention:** `make prune [KEEP=200]` deletes the oldest run records and
  their diffs beyond the keep-count (state files are never touched).
- **TaskEvent receiver:** `make hook-server` (port 4998) exposes
  `POST /hooks/taskevent` — the normalized trigger endpoint
  ([triggers/task-event-schema.json](../triggers/task-event-schema.json)) for Jira
  Automation rules, Bitbucket/Stash webhooks, and OpenHands. Events are validated,
  deduplicated on `sha256(mode|repo|pr|key-slot|updated|workflow_version)`.
  JIRA events place their required key in that slot; PR events deliberately keep
  the historical empty slot even when they carry the optional explicit `key`, so
  adding ticket linkage does not change SCM webhook replay identity. Redeliveries
  are no-ops (NFR-6). Accepted PR keys flow through the same queue validation as
  wizard/API intake. `AIQE_HOOK_AUTORUN=1` drains the queue after each accepted
  event; `AIQE_HOOK_TOKEN` requires an `X-AIQE-Token` header from senders.

## 5a. Measurement, review efficiency & knowledge reuse (shipped roadmap features)

### CI health and flake quarantine
Point CI at the receiver and the scorecard's "test health" becomes a live number:

```bash
curl --data-binary @results.xml -H "X-AIQE-Token: $TOKEN"      http://<receiver>:4998/hooks/ci/results        # raw JUnit XML, no wrapping
```

The response reports `matched`/`unmatched` so mapping rot shows up in the CI job's
own log. This route accepts up to **5 MB** (every other receiver route caps at 1 MB);
a larger post is refused with `413` before the body is read, so split an oversized
report rather than expecting it to be truncated — see
[deployment.md](deployment.md#request-limits-at-the-trigger-ingress). Then:

```bash
python3 bin/qa.py flaky                       # sometimes-passing tests, worst first
python3 bin/qa.py quarantine <test_id> --note "fails ~30% since build 812"
python3 bin/qa.py unquarantine <test_id>
```

Quarantine is a **catalog tag** plus a printed exclusion line that is a *proposal*
for the repo owner's CI — the platform never edits a test repo's config, and the
gate still gates changed specs (changing a flaky spec is exactly when it must pass).

### Traceability matrix
`make trace-matrix [KEY=..] [CSV=1]`, `GET /api/trace-matrix?format=csv`, or the
table atop the dashboard **Trace** view: one row per plan scenario — ticket →
scenario → generated spec → gate commit → CI health. An **approved scenario with no
test** is rendered outlined: a requirement someone signed off that nothing
exercises. This is the audit artifact.

### Risk-ranked gaps and drift alarms
`make gaps` now orders uncovered surface by deterministic risk (mutating method,
sensitive path token, state-addressing) with the reasons on each line — and the
ranked file feeds generation and the plan adversary. `make maintain` (cron it
nightly) additionally snapshots per-repo uncovered counts and notifies when a
repo's gaps **grew**.

**Read its exit code.** Maintenance steps are independent and best-effort — a
network blip in guidance sync must not skip the state-bundle snapshot that runs
after it — so the job does not stop at the first failure. What it does instead is
report, in three distinct outcomes:

| outcome | meaning | exit |
|---|---|---|
| `ok` | the step ran and succeeded | — |
| `DEGRADED` | it depends on an external system this platform does not own (SCM reachability, the embedding endpoint). Named in the summary; the job stays green, because one that reddens on somebody else's outage is one whose red gets ignored | 0 |
| `FAILED` | a local step that should have worked. Named, with its command and exit code | **1** |

A summary listing **every** step is printed on every run, including a clean one —
a summary that only appears when something is wrong trains people not to look for
it. Until 2026-08-04 the target ignored every step failure and printed
"maintenance complete" unconditionally, so a CronJob reported Success no matter
what happened; measured with two steps sabotaged (one of them the backup), the
last line was still `maintenance complete` and the exit code was still 0. On
OpenShift, `deploy/openshift/cronjob.yaml` runs it with `restartPolicy:
OnFailure`, which is only worth having because the exit code now means
something.

### Reviewing faster
- **In place:** every Artifacts panel carries Approve / Request-changes (note
  required) next to the rendered diff — one screen from code to decision.
- **In batch:** Runs & reviews' *Approve all shown* clears the filtered set after
  one confirmation; each decision is still recorded individually.
- **Assigned:** set `review.reviewers: [alice, bob]` in org-config and committing
  runs assign each key by stable hash (a re-commit returns to the reviewer with
  context). Assignment is a nudge — the decision records whoever actually acted.
- **Re-approval reviews the delta:** approving a plan snapshots the signed text;
  if it is edited later, the plan editor shows a unified diff against that
  baseline ("Changed since last approval") and the status has already dropped to
  draft.

### Plan reuse, mediated by you
When a plan resembles a prior one, the plan editor shows *"Similar prior plan:
KEY (n% · status)"* with the shared terms named and the prior text viewable
read-only. Nothing is ever auto-applied; an unrelated ticket shows nothing —
no match beats a stretched match.

### Moving knowledge between deployments
`make state-export` carries everything that is somebody's work;
`python3 engine/lib/state_bundle.py export --knowledge` carries only the
transferable wisdom — guidance, catalog, conventions, the plan corpus (which seeds
similar-plan retrieval for the receiving team) — and refuses run history, review
decisions and your registry topology. See [data-portability.md](data-portability.md).

### 5b. LLM cost: the Cost view and the cost levers

The **Cost** view shows spend by workflow, key, phase and model tier, per-phase
turn calibration (observed p50/p95 vs the configured ceiling) and prompt-cache
hit rates — every number badged **measured** or **simulated** (`~`), and savings
print `n/a` until at least one measured run exists. The same data serves
`make cost-report [DAYS=N]`, `qa.py status --cost` and `artifacts <KEY>`.
The task LLM total deliberately excludes separately labelled embedding and
cache-probe spend. Daily embedding rows retain their reported/estimated/unknown
basis; probe rows are attributed `probe`; and an **Unmeterable** line always
states how many unknown-basis phases and tasks exist, including zero. The
provider and basis tables cover all three consumer classes without turning an
unknown amount into `$0`.

For an auditable statement of one exact task key, run:

```bash
make cost-statement KEY=PROJ-301                 # Markdown export
make cost-statement KEY=PROJ-301 FORMAT=csv      # finance line-item export
python3 bin/qa.py cost-statement PROJ-301        # print without exporting
```

Exports land in `reports/exports/<KEY>-cost-statement.{md,csv}` with one row
per run phase. Reported, estimated and simulated dollars remain separate;
local usage is tokens, while unknown and started-but-unrecorded phases are
counts; a priced row missing its numeric amount is also counted as incomplete.
Probe/other non-user attribution is listed outside the task total. The
Artifacts view shows the same summary and download links, including plan-only
or aborted keys that deliberately have no run record. The JSON API is
`GET /api/cost-statement?key=<KEY>`; add `format=md|csv` for an export response.

Provider billing is accessed only through the LLM adapter family. Configure the
write-only `ANTHROPIC_ADMIN_KEY` in Settings (an organization Admin API key with
read-only usage/cost scope), then inspect the normalized provider result with:

```bash
make cost-reconcile DAYS=7             # configured default provider
make cost-reconcile DAYS=7 PROVIDER=mock
```

Every adapter implements `usage <window>`. Providers that cannot report billing
return an explicit `unavailable` state with no cost field; they never fabricate
zero spend. The command compares the adapter's UTC `[start, end)` window only
with same-provider, `reported` ledger evidence. It prints the two figures,
absolute/percentage drift, under/over direction, per-basis call and dollar
evidence, and the reconcilable share of same-provider call attempts. Dollar
bases remain separate; the share is call-weighted precisely so estimated,
simulated, local, unknown, and unrecorded evidence is disclosed without making
a blended dollar total. `auto_corrected` is always false.

Each run atomically publishes the latest reconciliation state and the Cost view
shows exactly one of **not reconciled**, **reconciled / no drift**, or
**reconciled / drift**. No credential, an unreachable billing API, missing or
invalid persisted evidence, and a provider timeout all remain **not
reconciled**—never a green zero. Drift above
`budgets.reconcile_drift_pct` (10% by default) sends a Notify-port alarm with
both figures, the provider window, and likely causes; it never corrects the
ledger. `make maintain` runs this check nightly: provider or notification
outages are named **DEGRADED**, while invalid configuration or a failed durable
write is **FAILED** and makes maintenance exit non-zero.
Set `AIQE_EXPORTS_DIR` to relocate generated exports independently; otherwise
they follow `AIQE_STATE_DIR` and default to `reports/exports`.

Settings → **Cost levers** holds one kill switch per mechanism: the phase cache
(`AIQE_PHASE_CACHE`), retrieval-scoped context (`AIQE_CONTEXT_SCOPE`; per-phase
policy in org-config `context_scope:`), the missing-context retry
(`AIQE_CONTEXT_RETRY`), the case-level test index (`AIQE_TESTCASE_INDEX`, default
off during the S1 preview), and semantic plan reuse (`AIQE_PLAN_REUSE`, default off
— a reused draft always lands for human review with a "Reused from" banner and
a VERIFY checklist). Budget envelopes per workflow and the degradation ladder
live in org-config `budgets:`; a run that degraded says so on the wizard's
generate step. After real runs exist, `make cost-baseline` freezes per-phase
medians and `make maintain` alarms on >25% regressions.

`make cache-probe` still requires real provider access and makes two billed
calls. It takes the normal pipeline lock and durably records those calls as
non-user `probe` activity even if the probe fails partway through; they appear
in the report's Probe section and never inflate a ticket's statement total.

Generated-test review is judgement work: `reviewer` and `reviewrepair` stay
on the capable tier even near the envelope. When review actually runs, the
effective PR, JIRA, or `tests` envelope is its unchanged base plus the
configured `review_uplift_usd` (currently a provisional $0.75 planning
allowance, not measured spend). Plan-only and disabled/off review receive no
uplift. Queue warnings show both parts of the effective cap, while an explicit
`MAX_COST_USD_PER_RUN` continues to override it.

`AIQE_ARTIFACT_REUSE=1` enables the durable second-level cache in addition to its
existing duplicate/learning preview behavior. The local phase cache is always
asked first; only its miss can become an artifact reuse. Identical prompt,
context, provider model, policy, run parameters, and generator version restore a
pure phase contract plus its declared plan/testdata product from B1 after a cache
clear or full-state move. `generate` and `validate` are never eligible because
their real output is workspace and git state. The Cost report shows phase-cache
hits and `Artifacts reused` separately, with avoided tokens marked reported or
estimated and no inferred dollar saving.

When the case-level index is enabled, rebuilds cover every registered E2E
repository. A complete `workspace/tests/<repo>` checkout is reused; an absent
repository is cloned read-only through its registered SCM adapter into the
derived knowledge-index cache. An unreachable repository does not abort the
estate rebuild: it is recorded as **NOT INDEXED** with a sanitized reason, and
the remaining repositories continue. Run `make index-stats` to see cases,
parsed/unparsed files, SCM exit class, and per-repository reasons. Nightly
`make maintain` rebuilds chunks before refreshing vectors, so unchanged chunk
hashes incur no embedding call.

The same flag also closes the same-run gap for generated tests. After a gate
successfully pushes a commit, the pipeline reads the committed bytes, upserts
only changed testcase chunks, and records the full commit/case/chunk provenance
before finalizing the run record. `no_changes`, quarantined, and clone-failed
gates never enter the index. Team approvals, changes requests, and typed
duplicate exclusions append to `reports/runs/testcase-provenance.jsonl`; they
do not rewrite indexed code or its SHA. Outcome-aware ordering is default-off
with `AIQE_ARTIFACT_REUSE` and, when enabled, only breaks equal retrieval scores.
An unavailable or corrupt provenance store is reported explicitly and ignored
for ranking rather than being interpreted as no review history.

### Durable task artifacts, historical explain, and reuse (B1–B3)

Set `AIQE_ARTIFACT_STORE=1` to enable the content-addressed store foundation.
It defaults to `reports/agent-artifacts/`, which is on the deployed reports
volume; `AIQE_ARTIFACTS_DIR` is the explicit isolation/placement override.
Writes above `AIQE_ARTIFACT_MAX_BYTES` (default 1 MiB), secret-shaped content,
unknown artifact kinds, and repo-owned guidance are rejected. Reads recompute
the content hash and quarantine damaged evidence rather than returning it.

`make prune KEEP=200` prunes artifact references alongside run records, and
`make maintain` does the same nightly. `AIQE_ARTIFACT_KEEP_RUNS` independently
configures how many producing runs' references are retained (default 200); a
quarantined reference makes blob sweeping conservative until an operator
resolves that evidence. The feature remains default-off, and B2 adds automatic
per-phase capture, historical bundles, and portable-state integration.

When enabled, each LLM phase archives the exact prompt and input files before the
provider call. The final run record points to a versioned bundle of B1 references
and hashes; skipped phases, full-estate fallbacks, failed captures, and artifact
kinds that were not produced are named explicitly. `make explain KEY=PROJ-123`
therefore answers historical context questions after `out/` has been cleaned. It
rejects corrupt content and a pointer belonging to another run instead of using
current scratch as a substitute.

`make state-export` includes this evidence in the full state profile, and
`make state-import` restores it to the configured artifact-store path. A
knowledge-only export intentionally excludes run-scoped artifact history.

When both `AIQE_ARTIFACT_STORE=1` and `AIQE_ARTIFACT_REUSE=1` are set, a later
identical pure phase can restore its durable product from the full-state store.
`make explain KEY=PROJ-123` names every hit, miss, phase-cache-owned result, and
unsafe-phase refusal from the historical run record.

## 6. Integration guide

Tool-specific step-by-step guides live in [integrations/](integrations/README.md):
[OpenHands](integrations/openhands.md) · [Jira + Confluence](integrations/jira.md) ·
[Bitbucket Cloud & Stash/Server](integrations/bitbucket-stash.md).

### 6.1 Trigger paths (all call the same `engine/pipeline.sh`)

| Path | Config | When to use |
|---|---|---|
| 1. OpenHands-native | `triggers/openhands/microagents/ai-qe.md` | Primary: label `ai-tests` on a PR / `ai-test-gen` on a ticket, or `@openhands` mention |
| 2. GitHub Actions / Bitbucket Pipelines | `triggers/github-actions/ai-qe-pr.yml`, `triggers/bitbucket-pipelines/` | Estates already governed by SCM CI |
| 3. Jenkins | `triggers/jenkins/Jenkinsfile` + generic webhook | Estates whose SDLC gates live in Jenkins |

JIRA side: an Automation rule fires a webhook on label `ai-test-gen`
(`triggers/jira-automation/webhook-setup.md`).

### 6.2 Onboarding a new repository

```bash
# Source repo (frontend or backend):
bin/onboard.sh source payments-api backend bitbucket workspace/payments-api payments openapi/payments.yaml

# Test repo:
bin/onboard.sh test e2e-api-tests-2 api github org/e2e-api-tests-2 node-test
```

`onboard.sh` is idempotent (re-registering is a no-op). It writes the registry entry,
prints the template drop-in steps, triggers catalog bootstrap for test repos when the
repo material is present, re-runs the routing goldens, and regenerates `AGENTS.md`.
Follow-ups: drop `templates/{source,test}-repo/*` into the actual repo and add a
trigger config.

### 6.2b Configuring existing application repositories

`bin/repos.py` manages registered repos after onboarding — the registry stays the
single source of truth, and every mutation validates references, re-runs the routing
goldens, and regenerates `AGENTS.md`:

```bash
make repos                                             # table of all app repos + coverage
python3 bin/repos.py show orders-api                   # full entry + harvested endpoints
python3 bin/repos.py set orders-api domains checkout,orders,returns
python3 bin/repos.py set orders-api contract openapi/orders-v2.yaml
python3 bin/repos.py link payments-api web-storefront-ui    # frontend consumes backend
python3 bin/repos.py unlink payments-api web-storefront-ui  # (contract fan-out follows)
python3 bin/repos.py remove old-service                # refuses while tests still map to it
```

`link`/`unlink` maintain both sides of the dependency graph (`consumed_by` +
`consumes_services`) — this is what drives contract-change fan-out to consumer UI test
repos. `remove` refuses if the catalog still maps tests to the repo, pointing you at
`bin/qa.py tests --app <name>` to remap first.

### 6.3 Onboarding a new team / estate

See [onboarding-new-team.md](onboarding-new-team.md) — fork the control-repo template,
fill the registry + org config, drop templates, wire a trigger path, bootstrap each test
repo, and gate on `make test-routing && make eval`. Target ≤ 1 day; the engine is never
modified.

### 6.4 Onboarding a new SDLC tool

See [onboarding-new-tool.md](onboarding-new-tool.md) — classify the tool against the six
ports, prefer MCP registration (`sandbox/mcp-setup.sh`) where an official MCP server
exists (one Atlassian MCP connection covers Jira + Confluence + Bitbucket), otherwise
write a thin CLI adapter implementing only that port's verbs (unknown verbs must exit
64), and add it to `adapters/conformance/test_adapters.sh`. Nothing in `engine/`,
`prompts/`, or `catalog/` changes.

## 7. Going real (`AIQE_MOCK=0`)

### Step one: the parity run (no credentials beyond claude CLI auth)

```bash
make parity-pr     # Workflow A: real claude -p phases, demo estate, mock adapters (~$0.30)
make parity-jira   # Workflow B (~$1.60)
```

`AIQE_REAL_LLM=1` (with `AIQE_MOCK=1`) swaps only the LLM phases for real `claude -p`
calls — adapters, estate, gate, and environment stay as in the demo. This validated
prompt quality end-to-end (see REVIEW.md Pass 5): real triage classification, generated
boundary tests executing against the live app, the repair loop, and never-guess open
questions. Run it after any prompt or org-config change.

### Full real mode

1. `cp .env.example .env` and fill in what your estate uses:
   `ANTHROPIC_API_KEY` (LLM phases), `GITHUB_TOKEN`/`BITBUCKET_TOKEN` (scoped:
   contents-RW on feature branches only), `ATLASSIAN_MCP_TOKEN` (service account —
   one credential covers Jira/Confluence/Bitbucket), `SLACK_WEBHOOK_URL`,
   `SPLUNK_HEC_*`, `JENKINS_*`, and per-run budget caps.
2. Register MCP servers in the sandbox: `sandbox/mcp-setup.sh` (Docker image in
   `sandbox/Dockerfile`).
3. Real runs use `make run-pr` / `make run-jira`. Phases now execute
   `claude -p` headlessly with per-phase `--allowedTools`/`--max-turns` from
   `org-config.yaml`; transcripts are archived under `out/` per run.
4. Real test repos typically set `commands.test: npx playwright test` — no engine or
   gate changes needed.

Security posture that does not change between modes: no LLM phase can push (the gate
owns git), ticket/PR/Confluence text is treated as data rather than instructions, and
every generated test must be born-mapped.

## 8. Known limitations (PoC)

Tracked in [REVIEW.md](../REVIEW.md) ("Open items"): Playwright execution validated
only via the framework-agnostic abstraction (demo runs `node --test`); state stores
are JSON files (honest at PoC scale — the PostgreSQL migration is an H2 item in
[product-direction.md](product-direction.md)). OpenHands is **optional** by design:
`AIQE_OPENHANDS=off|auto|required` sets how much an outage matters (see
[integrations/standalone-operation.md](integrations/standalone-operation.md)).

Two limitations previously listed here are **closed**, and the second is worth
knowing about if you are reading older notes:

- Mock phase stubs no longer bypass contract extraction. Every stub is wrapped as a
  provider reply (`engine/lib/mock_result.py`) and parsed by the same
  `extract_contract.py` the real path uses, so a stub that drifts from its schema
  now fails the demo instead of passing silently. Verified by renaming a required
  key in the analyze stub: `make demo-jira` exits 2 with `CONTRACT REJECTED`, where
  it previously exited 0.
- Prompt *quality on current HEAD* remains unmeasured here. A historical Pass-5
  parity run succeeded (see REVIEW.md), but `make parity-pr` / `parity-jira` refreshes
  are blocked on `claude` CLI authentication (`claude login`, or
  `ANTHROPIC_API_KEY` in `.env`). Treat Pass 5 as historical evidence, not a current
  provider-quality baseline.

## 9. Command index

Every `make` target, grouped. Details are in the section linked from each group.

| Group | Targets |
|---|---|
| Setup | `deps`, `bootstrap`, `agents`, `skills`, `config` |
| Demo (no credentials) | `demo-pr`, `demo-jira`, `demo-plan`, `demo-plan-tests`, `demo-requirements`, `demo-bootstrap` |
| Real runs | `run-pr`, `run-jira`, `queue-run`, `parity-pr`, `parity-jira`, `parity-compare` |
| Plan-first (§5) | `plan`, `plan-show`, `plan-edit`, `plan-approve`, `plan-changes`, `plan-link`, `plan-tests`, `plans` |
| Requirements & specs (§5a) | `requirements`, `requirements-approve`, `spec-verify`, `spec-savings` |
| Selective approval | `select`, `select-finalize` |
| Operations (§5) | `status`, `reviews`, `review-queue`, `coverage`, `gaps`, `critic`, `explain`, `trace-matrix`, `report`, `email`, `prune`, `maintain` |
| Services | `serve`, `hook-server`, `dashboard` |
| Estate & knowledge | `repos`, `repo-facts`, `repo-agents`, `sync-guidance`, `sync-status`, `catalog-db`, `ingest-results`, `index-rebuild`, `index-stats` |
| Plan sharing | `export-plan`, `publish-plan`, `attach-plan` |
| Cost (§5a) | `cost-report`, `cost-statement`, `cost-baseline`, `cache-stats`, `cache-clear`, `cache-probe` |
| State portability | `state-export`, `state-inspect`, `state-import`, `clear-demo` |
| Deployment | `docker-build`, `deploy-local`, `deploy-local-down`, `deploy-openshift` |
| Verification | `review` (everything below, in sequence), `test-routing`, `test-routing-adv`, `test-gate`, `test-state`, `test-providers`, `test-bootstrap`, `test-entrypoint`, `test-observability`, `conformance`, `eval`, `retrieval-eval`, `check-integrations`, `smoke-openhands` |

`make review` is the one to run before believing anything works: goldens, adapter
conformance, seven adversarial/smoke suites (gate, routing, state, providers,
bootstrap, entrypoint, observability), the replay benchmark and the scorecard. It takes
roughly twelve minutes.

`make retrieval-eval` runs the versioned A5 gold set directly. It reports
precision@5, recall@5, and MRR separately for deterministic, lexical, and
semantic ranking. Deterministic and lexical results always have independent
floors. If embeddings are absent, semantic is `unmeasured`; if mock embeddings
are active, it is `simulated` and does not gate quality. A configured real
provider must meet its semantic floors. Results in
`eval/results/retrieval-quality.json` include corpus/label hashes, source
commit, evaluation time, hostile-retrieval checks, and the current M9 human
baseline state. Update the v1 labels and pinned corpus hash together under QE
Lead review; never relabel drift implicitly.

Three verification targets are worth calling out because each exists to cover an
entry point that nothing was running:

- **`make test-bootstrap`** — the catalog bootstrap chain decides which app repos each
  test covers, and `covers:` decides routing. A chain that quietly produces less than
  it should *unroutes* work, which is the one failure this platform cannot see from
  the inside.
- **`make test-entrypoint`** — first-boot state seeding. See
  [deployment.md](deployment.md#first-boot-what-a-new-deployment-seeds).
- **`make test-observability`** — the transaction log and alert rules, including that
  an unreadable log reports `unevaluable` rather than `ok`.
