# Getting Started

This guide takes you from a fresh clone to a full end-to-end demonstration of both
workflows in about two minutes, then explains what you just saw. No API key, no
credentials, and no external services are needed for the demo — the LLM phases and all
external tools (GitHub, Jira, Slack, Splunk) are replaced by mock adapters that speak
the exact same verbs as the real ones.

## Prerequisites

| Tool | Why | Check |
|---|---|---|
| bash (Git Bash on Windows) | All orchestration is POSIX shell | `bash --version` |
| GNU make | Task runner | `make --version` |
| Python 3.10+ with `pyyaml`, `pytest` | Resolver, catalog, config parsing | `make deps` installs them |
| Node.js 18+ | Demo app-under-test and `node --test` runner | `node --version` |
| git | Workspace clones, the gate | `git --version` |

Works on Linux, macOS, and Windows (developed and verified under Git Bash on Windows 11).

## Setup

```bash
git clone <this-repo> && cd auto-tests-gen
make deps                # pip install pyyaml pytest
make test-routing        # sanity check: 5 golden tests should pass
```

That's it for demo mode. (`cp .env.example .env` and filling credentials is only needed
for real-estate runs — see the [User Guide](user-guide.md), section "Going real".)

## The two-minute demo

```bash
make demo-bootstrap      # catalog bootstrap: inventory + map existing tests
make demo-pr             # Workflow A: PR-triggered test sync
make demo-jira           # Workflow B: JIRA-triggered test authoring
make review              # all four review passes (goldens, conformance, adversarial gate, eval)
```

### What `make demo-bootstrap` shows

The catalog bootstrap crawls the two demo test repos, extracts evidence from every spec
(endpoints called, UI routes visited, fixtures used), correlates it against the app
repos' OpenAPI contracts and route tables plus JIRA-keyed git history, and tiers each
test by confidence:

```
auto         conf=0.95  PROJ-88: applies % discount    -> ['orders-api']        via ['contract_match', 'git_history']
auto         conf=0.95  PROJ-61: gets an order by id   -> ['orders-api']        via ['contract_match', 'git_history']
orphan       conf=0.0   old-inventory.spec             -> []                    via ['none']
auto         conf=0.95  PROJ-45: cart route is ...     -> ['web-storefront-ui'] via ['git_history', 'route_match']
```

Note the planted legacy test landing as an **orphan** — dead-test discovery working as
designed. The registry's `covers:` coverage map is then regenerated from the catalog
(never hand-edited), and a human review queue is exported to `catalog/review/*.csv`.

### What `make demo-pr` shows (Workflow A)

A fixture "PR" (#201 on `orders-api`, adding discount validation) flows through the full
pipeline: **resolve** (registry routes it to `e2e-api-tests-1`) → **triage** →
**generate** (a boundary-value spec + its born-mapped catalog sidecar) → **validate** →
**gate**. The gate boots the real demo Orders API on a random port, executes the
generated spec against it, tears the app down, scans for secrets, and commits:

```
[gate:e2e-api-tests-1] GATE_STATUS=COMMITTED 3137c12
[gate:e2e-ui-tests-1]  GATE_STATUS=NO_CHANGES
[mock-slack] AI-QE run ... for PR-orders-api-201:
- e2e-api-tests-1: committed ✅
- e2e-ui-tests-1: no changes ➖
```

The commit lands on branch `test/PR-orders-api-201-ai-qe` **inside the workspace clone**
(`workspace/tests/e2e-api-tests-1`) — inspect it with
`git -C workspace/tests/e2e-api-tests-1 show --stat`.

### What `make demo-jira` shows (Workflow B)

Fixture ticket `PROJ-301` ("Order discount validation", component=Checkout,
label=api-only) flows through **analyze** (with a mock linked-Confluence PRD) →
**testplan** (`testplans/PROJ-301.md`) → **testdata** (canonical cases in
`testdata/PROJ-301/`) → **generate** → **validate** → **gate**. The `api-only` label
correctly restricts routing to the API test repo, and the summary is posted back to the
(mock) JIRA ticket and Slack.

### The spec-driven demo (the full governed journey)

`make demo-jira` runs the pipeline end to end in one shot. This variant walks the
same ticket through the **governed** route, stopping where a human is meant to
decide. It is the demo to give when the question is "how do we control this?"
rather than "does it work?".

```bash
make requirements KEY=PROJ-301        # formalize the ticket into EARS statements, then STOP
make requirements-approve KEY=PROJ-301
make demo-plan KEY=PROJ-301           # author the plan (+ adversarial review), then STOP
make plan-show KEY=PROJ-301
make plan-approve KEY=PROJ-301
make demo-plan-tests KEY=PROJ-301     # resume: testdata -> generate -> validate -> gate
```

What each stop is for:

- **`make requirements`** writes `specs/PROJ-301/requirements.yaml` and comments on
  the ticket. If the ticket does not say what should happen, this is where you
  find out — a *blocking* ambiguity halts with exit 65 and a question rather
  than a guess. The cheapest artifact to change is a sentence.
- **`make demo-plan`** authors the plan and runs a **read-only adversary** against
  it before you are asked to approve. On the fixture ticket it reports
  `2 gap(s) raised, 2 high-severity, 2 accepted, 3 scenario(s) in the final plan`
  — the adversary may only ADD scenarios, so it changes what you approve, never
  whether you are asked.
- **`make plan-approve`** *signs* the plan against a content hash. Editing an
  approved plan revokes the approval, so "approved" always names text somebody
  read.

Then see the result three ways:

```bash
python3 engine/lib/spec_workflow.py   # where the ticket is, and what is enforcing it
make trace-matrix KEY=PROJ-301        # ticket -> scenario -> spec -> gate commit -> CI
make spec-savings                     # scenarios a cataloged test already covers
```

The workflow board prints `live` once the gate has committed — and prints the
governance line above it, which on a default estate reads:

```
requirements gate: off  ·  spec enforce: off
  NOTE: nothing here is enforced — every step below is advisory.
```

That is the honest answer, and it is the point of the demo: the process is
**visible** before it is **mandatory**. Turn it on in Settings when the signal
looks clean — `warn` first, `strict` after. The trace matrix will show two of the
three scenarios with no test, which is exactly what `strict` would refuse to
commit.

All of this is in the UI too, under **Spec workflow** — see
[ui-guide.md](ui-guide.md).

### After the demos: monitor, query, manage

```bash
python3 bin/qa.py artifacts PROJ-301 --full   # view the generated plan + test code
make status          # runs with per-repo gate outcomes, team review + release columns
make serve           # interactive dashboard :4999 — fifteen views: Overview, Guided run, Run progress,
                     #   Intake &
                     #   queue, Test plans (review/edit/approve), Runs & reviews, Cost,
                     #   Trace (story→plan→tests→gate→review→release timeline),
                     #   Artifacts (rendered code + before/after diff), Test catalog,
                     #   Repositories (add/edit/map repos + per-repo guidance + SCM
                     #   sync), Settings (integrations -> .env, clear demo data)
python3 bin/qa.py trace PROJ-301   # the same traceability timeline on the CLI
make demo-plan       # plan-first: author a plan from PROJ-301 and STOP for review
                     #   then: make plan-approve KEY=PROJ-301 && make demo-plan-tests
make sync-guidance   # pull repo-owned AGENTS.md/CLAUDE.md from the SCM (make sync-status)
make reviews         # team-review board (qa.py mark <KEY> approved --by you)
make coverage        # app-repo x test-repo matrix; make gaps for uncovered surface
make report DAYS=7 FORMAT=pdf                 # team status report (completed/queue/health)
make export-plan KEY=PROJ-301 FORMAT=pdf      # shareable export (also docx/html/md)
python3 bin/qa.py run-inline "Bug: ...\nAC-1: ..." --repos orders-api --type Bug
make repos           # configure repos: add-app/add-test/scope/notes (covers = evidence ∪ scope)
make config          # which aiqe.properties file is loaded + keys it sets (going real:
                     #   credentials can come from aiqe.properties < .env < environment)
make ingest-results FILE=eval/benchmark/results/junit-sample.xml   # CI health demo
python3 bin/qa.py sql "SELECT title, pass_rate FROM tests"         # catalog index
```

Also notice `AGENTS.md` at the repo root — the auto-generated estate-knowledge file
(live endpoints, routes, existing coverage, conventions) that gets injected into every
LLM phase. It was refreshed by the runs you just did; never edit it by hand
(`make agents` regenerates it).

### What `make review` shows

Four passes in sequence — all must be green:

1. **Routing goldens** — 5 pytest cases pinning resolver behavior (fan-out on contract
   change, docs-only skip, ambiguity → ask-a-human).
2. **Adapter conformance** — every adapter answers its port's verbs; unknown verbs exit 64.
3. **Adversarial gate suite** — four attacks (planted credential, out-of-scope write,
   unmapped test, failing test) must each be blocked with the correct exit code.
4. **Benchmark replay + scorecard** — routing accuracy across the fixture set (target ≥95%).

## Where things land

| Path | Contents |
|---|---|
| `workspace/` | Per-run clones — `src/` read-only sources, `tests/` writable test repos (gitignored scratch) |
| `out/` | Phase JSON contracts, resolution output, mock adapter logs (gitignored) |
| `reports/` | Gate execution logs per run: `<KEY>-<test_repo>.log` |
| `reports/runs/` | Persistent run records + archived gate-commit diffs (committable QA history); also holds the locked state files `reviews.json` (team review + release), `queue.json` (work queue), `hooks-seen.json` (webhook dedupe) |
| `reports/dashboard.html`, `reports/catalog.db`, `reports/exports/` | Generated dashboard, SQLite catalog index, plan exports (all gitignored, regenerable) |
| `catalog/` | Test catalog JSONL per test repo + review queues + `health.json` (CI pass rates/flakiness) |
| `AGENTS.md` | Generated estate knowledge injected into LLM phases, with `[NO TEST]` coverage-gap annotations (never hand-edit) |
| `testplans/`, `testdata/` | Workflow B artifacts (ticket-keyed) |

## Troubleshooting

- **Gate exits 6 (`GATE_REFUSED`)** — the gate refuses to run anywhere that resolves to
  this scaffold's own git repo. This is a safety backstop; it means a workspace clone
  was created without `.git`. Always clone through `adapters/mock/scm.sh` (which
  git-initializes copies), never a bare `cp -r`.
- **`require is not defined in ES module scope`** — a `package.json` with
  `"type": "module"` exists in an ancestor directory. Each demo repo carries its own
  `package.json` to prevent this; if you add a demo repo, include one.
- **`APP_START_FAILED` in a gate log** — the app-under-test didn't come up; the app's
  stdout/stderr is echoed into the same log. Check `/tmp/aiqe-env.log` too.
- **Windows: `UnicodeEncodeError` from a Python script** — the console is cp1252;
  reconfigure stdout to UTF-8 at the top of the script (see `eval/scorecard.py`).
- **Stale state between runs** — `rm -rf workspace out` resets all per-run scratch;
  everything in them is regenerated.
- **`CONTRACT REJECTED: the <phase> stub does not satisfy …schema.json`** — a demo run
  exited 2 because a mock stub drifted from its phase schema. This is working as
  intended and the message names the file to fix: since the mock harness started
  wrapping each stub as a provider-shaped reply and parsing it with the same
  `extract_contract.py` the real path uses, a stub can no longer disagree with its
  schema silently. Fix the stub in `engine/phases/mock_phase.sh`, not the check.
- **The receiver answers `413` or `400` to a webhook** — the trigger ingress caps
  request bodies (1 MB, or 5 MB on `/hooks/ci/results`) and refuses an oversized
  declaration before reading it. A `400` means the `Content-Length` was unparseable,
  negative, or larger than what the client actually sent. See
  [deployment.md](deployment.md#request-limits-at-the-trigger-ingress).

## Next steps

**Have a real job to do?** `docs/use-cases.md` is organised by the task —
"a PR changed a service", "a ticket needs coverage", "a test is flaky",
"what does this cost" — with the commands and what you should see.


- [User Guide](user-guide.md) — configuration reference, integration paths, onboarding
  real repositories, switching off mock mode.
- [Architecture diagrams](diagrams.md) — rendered views of the system.
- [architecture.md](architecture.md) — the full solution architecture (v2.1); code
  comments reference its section numbers (§5.8 etc.).
