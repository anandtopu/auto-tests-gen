# User Guide

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
| 7/8 | `APP_START_FAILED` / `APP_REPO_NOT_FOUND` | Environment provisioning failure (from `with-env.sh`) |

Non-zero exits quarantine the run for human inspection; they are never auto-retried.
The adversarial suite (`make test-gate`) permanently regression-tests codes 2–5.

## 3. Configuration reference

Three layers, each overriding the previous (§5.10): platform defaults → org config →
per-repo config.

### `registry/org-config.yaml` (org layer)

```yaml
models:            # model tier per phase; escalate after 2 failed generate attempts
phases:            # per-phase max_turns + allowedTools whitelist (least privilege)
resolution:
  confidence_threshold: 0.8   # below this the pipeline asks a human instead of guessing
catalog:
  auto_accept_confidence: 0.85
  review_band: [0.5, 0.85]    # between these → human review queue; below → orphan
budgets:           # per-run cost ceilings
adapters:          # which adapter script serves each port
```

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
run per key with expandable plan, scenarios, data, and test-code blocks.

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

### Interactive dashboard: fetch by release & manual work queue

```bash
make serve        # http://localhost:4999 — the dashboard with live actions
```

The dashboard (implemented from the "QA Dashboard" Claude Design) is a five-view app
with sidebar navigation: **Overview** (KPI tiles, a needs-attention feed, the coverage
matrix), **Intake & queue**, **Runs & reviews**, **Artifacts**, and **Test catalog** —
with toast feedback and pending-work badges on the nav.

Served (rather than opened as a file), the **Intake & queue** view becomes active:
pick a release, *Fetch items* lists the JIRA tickets targeting that fixVersion (via
the Tracker port's `search_release` verb — JQL in real mode, benchmark fixtures in
mock) plus known PRs whose tracked release matches, and each row has a *Queue*
button. *Run queue* drains the queue — items run through `engine/pipeline.sh`
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
  (from review decisions), and **test health/flakiness**.
- **SQLite catalog index:** `make catalog-db` builds `reports/catalog.db` (gitignored;
  JSONL stays the committed source of truth) — rebuilt automatically by bootstrap,
  mapping edits, and results ingest. Ad-hoc queries:
  `bin/qa.py sql "SELECT title, pass_rate FROM tests WHERE flaky=1"` (read-only).
  Tree-sitter-based extraction stays a flagged real-estate upgrade (needs native
  grammar packages the stdlib-only toolchain doesn't ship).

### Team-scale operations (P1)

- **Run isolation & parallel gates:** the pipeline takes an exclusive per-checkout lock
  (`out/.pipeline.lock` — waits up to 2 min, breaks stale locks after 30) because
  `workspace/` and `out/` are shared scratch; parallel capacity comes from one
  sandbox/checkout per run (OpenHands). *Within* a run, per-test-repo gates execute in
  parallel, each booting its own app instance on an OS-assigned free port.
- **Dashboard auth:** set `AIQE_UI_TOKEN` before `make serve` and every request needs
  the token — first browser visit via `/?token=<value>` (sets an HttpOnly cookie),
  API clients via `Authorization: Bearer <value>`. Unset = auth off (localhost dev).
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
  **deduplicated on `sha256(mode|repo|pr|key|updated|workflow_version)`** (webhook
  redeliveries are no-ops, NFR-6), and enqueued; `AIQE_HOOK_AUTORUN=1` drains the
  queue after each accepted event, `AIQE_HOOK_TOKEN` requires an `X-AIQE-Token`
  header from senders.

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

Tracked in [REVIEW.md](../REVIEW.md) ("Open items"):
real-LLM parity run pending an API key; mock phase stubs bypass JSON-schema contract
extraction (real path validates); Playwright execution validated only via the
framework-agnostic abstraction (demo runs `node --test`); OpenHands Path-1 live wiring
scheduled for rollout weeks 3–4.
