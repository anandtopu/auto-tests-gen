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

### After the demos: monitor, query, manage

```bash
python3 bin/qa.py artifacts PROJ-301 --full   # view the generated plan + test code
make status          # runs with per-repo gate outcomes, team review + release columns
make serve           # interactive dashboard :4999 — fetch by release, queue runs,
                     #   run from pasted JIRA text, export/publish/attach plans
make reviews         # team-review board (qa.py mark <KEY> approved --by you)
make coverage        # app-repo x test-repo matrix; make gaps for uncovered surface
make export-plan KEY=PROJ-301 FORMAT=pdf      # shareable export (also docx/html/md)
python3 bin/qa.py run-inline "Bug: ...\nAC-1: ..." --repos orders-api --type Bug
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

## Next steps

- [User Guide](user-guide.md) — configuration reference, integration paths, onboarding
  real repositories, switching off mock mode.
- [Architecture diagrams](diagrams.md) — rendered views of the system.
- [architecture.md](architecture.md) — the full solution architecture (v2.1); code
  comments reference its section numbers (§5.8 etc.).
