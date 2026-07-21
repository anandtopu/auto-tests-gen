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

## Open items (ticketed, not blocking)
1. Real-LLM parity run (`AIQE_MOCK=0`) once an API key is available — mechanics proven, prompt quality unmeasured here.
2. Mock stubs should pass through `extract_contract.py` for full schema parity (Pass 2 deviation).
3. Playwright execution unproven in this sandbox (browser CDN blocked) — framework abstraction verified via node-test; validate Playwright path in week 1 of real rollout.
4. OpenHands Path-1 live wiring (weeks 3–4 of the delivery plan); Path-2 mechanics fully proven.

**Verdict: build phases B1–B5 complete; all four review passes green; PoC is integration-ready by demonstration, not assertion.**
