# AI QE Platform — Proof of Concept Scaffold

Agentic SDLC test-engineering platform: PR-triggered E2E test sync (Workflow A) and
JIRA-triggered test authoring (Workflow B) across a multi-repo estate, powered by
**OpenHands** (orchestration + sandbox) and **Claude Code** (headless agent runtime).

## Documentation

| Doc | What it covers |
|---|---|
| [docs/getting-started.md](docs/getting-started.md) | Zero-to-demo in two minutes, expected output, troubleshooting |
| [docs/user-guide.md](docs/user-guide.md) | Operating the platform: repositories & mapping, per-repo AGENTS/CLAUDE guidance, team reports, Settings/integrations, dashboard, gate protocol, catalog lifecycle, going real (`AIQE_MOCK=0`) |
| [docs/integrations/](docs/integrations/README.md) | Step-by-step tool integration: OpenHands, Jira, Bitbucket Cloud & Stash/Server |
| [docs/diagrams.md](docs/diagrams.md) | Rendered architecture diagrams (Mermaid) |
| [docs/deployment.md](docs/deployment.md) | Deploying as a service: local Docker Compose + remote OpenShift / Kubernetes |
| [docs/architecture.md](docs/architecture.md) | Full solution architecture (v2.1) — code comments cite its § numbers |
| [docs/onboarding-new-team.md](docs/onboarding-new-team.md) | Adopting the platform for a new estate (≤1 day) |
| [docs/onboarding-new-tool.md](docs/onboarding-new-tool.md) | Adding a new SDLC tool behind the six ports |
| [implementation-plan.md](implementation-plan.md) · [REVIEW.md](REVIEW.md) | Build phases B1–B5 and the multi-pass review record |

## Layout

```
registry/    Repo registry + org config + routing golden tests
bin/         Operator CLIs & services: qa.py (monitoring/mappings/exports/inline runs),
             repos.py (repo config), dashboard_server.py (interactive UI),
             taskevent_receiver.py (webhook endpoint), smoke-openhands.sh,
             onboard.sh, gen_agents_md.py, with-env.sh
catalog/     Test Catalog schema, bootstrap pipeline (extract→correlate→classify→review),
             health.json (CI pass rates), SQLite index builder
engine/      Core pipeline: resolver, phase runner (claude -p wrapper), deterministic gate
prompts/     Versioned phase prompts (tool-agnostic; adapters injected at runtime)
skills/      Claude Code skills per test discipline (UI / API conventions)
adapters/    Six ports: scm, tracker, knowledge, cicd, notify, telemetry
triggers/    Path 1: OpenHands microagents | Path 2: GH Actions / Bitbucket | Path 3: Jenkins
templates/   Drop-in files for source repos and test repos (CLAUDE.md, config)
sandbox/     Docker image + MCP registration for the execution environment
eval/        Benchmark replay harness + scorecard
deploy/      Deployment artifacts: local/ (Docker Compose) + openshift/ (K8s manifests)
Dockerfile   Platform service image (dashboard + receiver + pipeline; OpenShift-ready)
```

## Quick start

```bash
# Demo (no credentials needed)
make deps                       # python deps
make demo-bootstrap             # catalog bootstrap on the demo estate
make demo-pr                    # Workflow A end-to-end (mock LLM, real gate/env/git)
make demo-jira                  # Workflow B end-to-end
make review                     # full regression: goldens + conformance + gate attacks + eval

# Real runs (cp .env.example .env, fill credentials)
make parity-pr / parity-jira    # real claude -p phases against the demo estate (~$2)
make run-pr REPO=... PR=...     # real Workflow A     make run-jira KEY=PROJ-123
make smoke-openhands            # staged live smoke test of the OpenHands integration
python3 bin/qa.py run-inline "<pasted JIRA text>" --repos orders-api --type Bug

# QA operations (bin/qa.py + services)
make serve                      # interactive dashboard :4999 — 7 views: Overview,
                                #   Intake & queue, Runs & reviews, Artifacts, Test
                                #   catalog, Repositories (add/edit/map + guidance),
                                #   Settings (integrations -> .env, clear demo data)
make hook-server                # TaskEvent webhook receiver :4998 (dedupe + enqueue)
make status / reviews / coverage / gaps    # runs, team review board, matrix, coverage gaps
make report [DAYS=7] [RELEASE=x] [FORMAT=pdf]   # team status report (completed work,
                                #   queue, throughput, estate health)
make queue-run                  # drain the manual work queue
make ingest-results FILE=junit.xml         # CI results -> per-test health/flakiness
make clear-demo [DRY=1]         # delete generated demo data (estate kept)

# Sharing & knowledge
make export-plan KEY=... [FORMAT=pdf|docx|html]   # shareable test-plan export
make publish-plan KEY=...       # one-way mirror to Confluence
make attach-plan KEY=...        # attach the plan to the JIRA ticket
make agents / catalog-db / prune           # estate knowledge, index, retention
make repos                      # repo config (add-app/add-test/scope/notes) — CLI +
                                #   dashboard Repositories view; covers = evidence ∪ scope
```

QA operations (monitoring, review/release tracking, work queue, exports, inline runs)
live in `bin/qa.py` — see [docs/user-guide.md](docs/user-guide.md) §5.

## Demo state
The registry ships with `covers:` pre-seeded from `catalog/catalog.sample.jsonl` so the
benchmark fixtures pass out of the box. In a real estate, run `make bootstrap` per test
repo first — coverage maps are always regenerated from the catalog, never hand-edited.

## Non-negotiables baked into this scaffold
- The gate (`engine/gate/gate.sh`) is the ONLY place `git push` happens. No LLM phase pushes.
- Every generated test is born-mapped (catalog entry in the same commit) or the gate rejects it.
- Ticket/PR/Confluence text is DATA, never instructions (see prompts).
- Per-phase `--allowedTools` and `--max-turns` are defined in `registry/org-config.yaml`.
