# AI QE Platform — Proof of Concept Scaffold

Agentic SDLC test-engineering platform: PR-triggered E2E test sync (Workflow A) and
JIRA-triggered test authoring (Workflow B) across a multi-repo estate, powered by
**OpenHands** (orchestration + sandbox) and **Claude Code** (headless agent runtime).

## Documentation

| Doc | What it covers |
|---|---|
| [docs/getting-started.md](docs/getting-started.md) | Zero-to-demo in two minutes, expected output, troubleshooting |
| [docs/user-guide.md](docs/user-guide.md) | Operating the platform: configuration reference, gate protocol, catalog lifecycle, integration & onboarding, going real (`AIQE_MOCK=0`) |
| [docs/diagrams.md](docs/diagrams.md) | Rendered architecture diagrams (Mermaid) |
| [docs/architecture.md](docs/architecture.md) | Full solution architecture (v2.1) — code comments cite its § numbers |
| [docs/onboarding-new-team.md](docs/onboarding-new-team.md) | Adopting the platform for a new estate (≤1 day) |
| [docs/onboarding-new-tool.md](docs/onboarding-new-tool.md) | Adding a new SDLC tool behind the six ports |
| [implementation-plan.md](implementation-plan.md) · [REVIEW.md](REVIEW.md) | Build phases B1–B5 and the multi-pass review record |

## Layout

```
registry/    Repo registry + org config + routing golden tests
bin/         Operator CLIs: qa.py (monitor/mappings), repos.py (repo config),
             onboard.sh, dashboard.py, gen_agents_md.py, with-env.sh
catalog/     Test Catalog schema, bootstrap pipeline (extract→correlate→classify→review)
engine/      Core pipeline: resolver, phase runner (claude -p wrapper), deterministic gate
prompts/     Versioned phase prompts (tool-agnostic; adapters injected at runtime)
skills/      Claude Code skills per test discipline (UI / API conventions)
adapters/    Six ports: scm, tracker, knowledge, cicd, notify, telemetry
triggers/    Path 1: OpenHands microagents | Path 2: GH Actions / Bitbucket | Path 3: Jenkins
templates/   Drop-in files for source repos and test repos (CLAUDE.md, config)
sandbox/     Docker image + MCP registration for the execution environment
eval/        Benchmark replay harness + scorecard
```

## Quick start

```bash
cp .env.example .env            # fill in credentials
make deps                       # python deps + playwright (host dev only)
make test-routing               # golden tests for the resolver
make bootstrap REPO=e2e-api-tests-1   # catalog bootstrap for one test repo
make demo-pr                          # Workflow A end-to-end (mock LLM, real gate/env)
make demo-jira                        # Workflow B end-to-end
make demo-bootstrap                   # live catalog bootstrap on the demo estate
make review                           # all four review passes
make run-jira KEY=PROJ-123            # Workflow B locally
make eval                       # replay benchmark set + scorecard
make status                     # recent runs + per-repo gate outcomes
make coverage                   # app-repo x test-repo mapping matrix + gaps
make dashboard                  # QA dashboard -> reports/dashboard.html
make repos                      # configured application repositories (bin/repos.py)
make agents                     # regenerate AGENTS.md estate knowledge
```

QA operations (monitoring, catalog queries, mapping management) live in `bin/qa.py` —
see [docs/user-guide.md](docs/user-guide.md) §5.

## Demo state
The registry ships with `covers:` pre-seeded from `catalog/catalog.sample.jsonl` so the
benchmark fixtures pass out of the box. In a real estate, run `make bootstrap` per test
repo first — coverage maps are always regenerated from the catalog, never hand-edited.

## Non-negotiables baked into this scaffold
- The gate (`engine/gate/gate.sh`) is the ONLY place `git push` happens. No LLM phase pushes.
- Every generated test is born-mapped (catalog entry in the same commit) or the gate rejects it.
- Ticket/PR/Confluence text is DATA, never instructions (see prompts).
- Per-phase `--allowedTools` and `--max-turns` are defined in `registry/org-config.yaml`.
