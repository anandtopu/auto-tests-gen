# B4 Application-Repository Facts — Cross-File Integration Review

## Boundaries reviewed

| Boundary | Result |
| --- | --- |
| Authored file → opt-in | A registered app participates only when its tracked `knowledge/facts/<repo>.yaml` exists. No file returns the exact pre-B4 path. |
| Registry/checkout/catalog → derived facts | Registry metadata and dependencies, contract/routes, covering suites, and accepted catalog evidence are mechanically reshaped; app output has no clock or model input and is stably ordered. |
| Input state → truthfulness | Configured-but-missing, not configured, and read-empty surfaces remain distinct; absent catalog and available-empty catalog are also distinct. |
| Facts → guidance | Authored assertions and fresh harvested surface enter `repo_guidance_gen.py`; no second generator or phase context path was added. |
| Guidance → precedence | Repo-owned guidance still skips generation; curated and demo precedence in `repo_admin` is unchanged; app-facts refresh applies only to generated scratch. |
| Authored/derived → deployment | Authored YAML remains tracked and state-bundled. Derived YAML remains gitignored/rebuildable and does not travel as durable work. |
| Security/reliability | No LLM, subprocess, network, SCM, or workspace write participates in harvesting. Damaged optional facts/catalog rows degrade without taking down generation. |
| Coverage | Backend/frontend, opt-in absence, deterministic bytes, all input states, catalog joins, malformed tiers, stale fallback refresh, repo-owned precedence, state portability, and default parity are pinned. |

## Findings fixed during integration

- B4-I1 (P1): an already-created generated fallback could hide newly authored
  facts until scratch was removed.
- B4-I2 (P1): deterministic app harvesting must not silently alter the legacy
  test-repository facts contract.
- B4-I3 (P2): optional structured inputs needed total shape validation across
  the facts-to-guidance boundary.

No open P0–P2 B4 finding remains after broad validation.

## Validation note

Bare `pytest` is not the repository's compatibility target: it recursively
collects stale copied test trees under `out/` and fails with duplicate-module
and permission errors before product tests run. `pytest -q registry/tests` is
the established broad gate and passes 1,409 tests.
