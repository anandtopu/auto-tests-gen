# Token-cost final per-file analysis

## Scope

Release-readiness review of the files changed by TCA-A1 through TCA-C4 at
`74d97ec`. Generated `AGENTS.md` contains only an unrelated timestamp refresh
and is excluded.

## Findings

| ID | Severity | File/surface | Line | Finding | Impact | Recommended fix |
| --- | --- | --- | ---: | --- | --- | --- |
| — | — | All scoped files | — | No new actionable P0–P2 finding | No release blocker identified | None |

## Per-file results

| Files | Review result |
| --- | --- |
| `engine/pipeline.sh`, `engine/phases/{run_phase,mock_phase}.sh` | Pass. One chained exit handler preserves original status, flushes started calls, releases the lock, and leaves plan/requirements without run records. |
| `engine/lib/{budget,spend_ledger,spend_history}.py` | Pass. Live TSV enforcement remains separate; durable rows are atomic/relocatable; source collisions deduplicate without losing attempts or blending bases. |
| `engine/lib/{cost_report,cost_statement,parity_compare,pr_comment,vector_index}.py`, `bin/{qa.py,cache-probe.sh}` | Pass. Historical consumers use the union, probe/embedding activity stays separate, and exact-key totals remain partitioned. |
| `engine/lib/{provider_usage,cost_reconcile,maintenance}.py`, `adapters/{llm,mock}/`, `adapters/conformance/test_adapters.sh` | Pass. Provider billing crosses only the usage port; unavailable is not zero; reconciliation is read-only and maintenance distinguishes external exit 75 from local failure. |
| `bin/{dashboard_server.py,dashboard.py}` | Pass. Cost API/UI render incomplete and three-state reconciliation evidence without treating absent data as healthy. |
| `engine/lib/{app_paths,demo_data,state_bundle,settings_store}.py`, `.gitignore`, `.env.example`, `aiqe.properties.example`, `deploy/openshift/secret.example.yaml`, `registry/org-config.yaml` | Pass. State relocation, clear/export lifecycle, write-only credential declaration, name-shaped ignores, and threshold configuration agree. |
| `Makefile`, `README.md`, `docs/{architecture,user-guide,integrations/README}.md` | Pass. Commands, boundaries, explicit bases, and operational failure states match the implementation. |
| `eval/token_cost_coverage.py` and TCA registry tests | Pass. Adversarial paths, consumer enumeration, deduplication, state lifecycle, API behavior, and source-boundary pins are executable. |

## Validation

81 focused tests, adapter conformance, M1 8/8, 1,767 registry tests, release
Ruff checks, Python compilation, Bash syntax, and four runtime entry-point
smokes passed.

## Open Questions

None for code release. Real M4 drift requires environment-owned Admin billing
authorization and must remain `not reconciled` until supplied.
