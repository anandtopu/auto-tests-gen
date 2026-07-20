# AI QE Platform — Proof of Concept Scaffold

Agentic SDLC test-engineering platform: PR-triggered E2E test sync (Workflow A) and
JIRA-triggered test authoring (Workflow B) across a multi-repo estate, powered by
**OpenHands** (orchestration + sandbox) and **Claude Code** (headless agent runtime).

See `docs/architecture.md` for the full solution architecture (v2.1).

## Layout

```
registry/    Repo registry + org config + routing golden tests
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
make run-pr  REPO=orders-api PR=123   # Workflow A locally
make run-jira KEY=PROJ-123            # Workflow B locally
make eval                       # replay benchmark set + scorecard
```

## Demo state
The registry ships with `covers:` pre-seeded from `catalog/catalog.sample.jsonl` so the
benchmark fixtures pass out of the box. In a real estate, run `make bootstrap` per test
repo first — coverage maps are always regenerated from the catalog, never hand-edited.

## Non-negotiables baked into this scaffold
- The gate (`engine/gate/gate.sh`) is the ONLY place `git push` happens. No LLM phase pushes.
- Every generated test is born-mapped (catalog entry in the same commit) or the gate rejects it.
- Ticket/PR/Confluence text is DATA, never instructions (see prompts).
- Per-phase `--allowedTools` and `--max-turns` are defined in `registry/org-config.yaml`.
