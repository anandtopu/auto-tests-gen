# Implementation Plan — AI QE Platform PoC (Build Edition)

Maps the 8-week delivery plan (architecture §10) to concrete build phases. Phases B1–B5
are executable NOW against the in-repo demo estate; weeks map to real-estate rollout.

| Build phase | Delivers | Proven by | Real-estate week |
|---|---|---|---|
| **B1. Demo estate + env provisioning (closes G5)** | Mock app repos (orders-api w/ runnable Express app + OpenAPI; web-storefront-ui w/ route table) and test repos (e2e-api-tests-1, e2e-ui-tests-1) as real git repos with JIRA-keyed history; `test_env` registry fields; env lifecycle scripts | Repos build; app serves; `with-env.sh` runs tests against a live app with guaranteed teardown | 1 |
| **B2. Catalog bootstrap — live run** | Extract → correlate → tier executed on both demo test repos; coverage maps regenerated from catalog; review queue exported | Catalog JSONL exists with evidence+confidence; `covers[]` regenerated; routing goldens still green | 1–2 |
| **B3. Workflow A end-to-end** | Full PR run: resolve → triage → generate → validate (tests execute against the RUNNING app) → gate commits to a real branch. LLM phases stubbable (`AIQE_MOCK=1`) so pipeline mechanics are testable without API spend; gate reads per-repo commands from `.ai-qe/config.yaml` | Demo run exits green; commit exists on `test/PR-…-ai-qe`; gate blocks a planted secret + scope violation | 3 |
| **B4. Workflow B end-to-end** | Fixture ticket → analyze → plan → data → tests → validate → gate; plan/data artifacts land in control repo, tests in test repo; JIRA/Slack via mock adapters (same port verbs) | Demo run green; testplan + canonical data + mapped test committed | 4 |
| **B5. Integration readiness (new-repo onboarding)** | `bin/onboard.sh` — registers any new source/test repo (registry entry + template drop + bootstrap trigger); demonstrated by onboarding `catalog-api` mid-stream and re-running routing | Onboard runs; goldens + fixtures still green with 3rd repo present | 5+ |
| **R1–R4. Multi-pass review** | Pass 1 functional · Pass 2 architecture conformance · Pass 3 security/reliability · Pass 4 integration readiness — findings + fixes in `docs/REVIEW.md` | All passes recorded; every finding fixed or ticketed | continuous |

## Real-estate rollout deltas (what changes outside the sandbox)
- `AIQE_MOCK=0`: phases call `claude -p` (key required); demo stubs unused.
- Frameworks: demo repos use `node --test`; real repos set `commands.test: npx playwright test`
  in `.ai-qe/config.yaml` — the gate is framework-agnostic by config (B3 change).
- Adapters: swap `adapters/mock/*` for real GitHub/Bitbucket/Jira/Slack/Splunk (same verbs —
  conformance-tested), register Atlassian MCP via `sandbox/mcp-setup.sh`.
- Environment (G5 closed): each test repo's registry entry carries
  `test_env: {mode: compose|shared, url|compose_file, seed_cmd}`. `compose` = hermetic
  app-under-test per run (default for API suites); `shared` = QA env URL (UI suites until
  hermetic UI env exists). `bin/with-env.sh` consumes this uniformly.
