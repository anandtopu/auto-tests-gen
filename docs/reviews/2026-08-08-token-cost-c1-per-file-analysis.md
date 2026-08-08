# Per-file Analysis: TCA-C1 Per-task Cost Statement

Date: 2026-08-08

| File | Responsibility | Local Status | Findings | Test/Validation Gap | Action |
|---|---|---|---|---|---|
| `engine/lib/cost_statement.py` | Exact-key model, basis totals, Markdown/CSV, atomic export | OK after fixes | Missing numeric priced amounts initially read as zero; Markdown/CSV cells needed injection hardening | Covered by mixed-basis, malformed-price and formula tests | Added explicit incomplete count and cell sanitization |
| `engine/lib/app_paths.py` | Relocatable mutable paths | OK after fix | New writer initially had no export path knob | Resolver/default/state-root pins added | Added `exports_dir()` and `AIQE_EXPORTS_DIR` |
| `Makefile` | Documented task entry point | OK | Requires KEY and exports md/csv | Real Make invocation passed | None |
| `bin/qa.py` | Statement CLI and artifacts-adjacent summary | OK after fix | Full statement in artifacts produced hundreds of lines | CLI/artifact regression covered | Compact summary plus drill-down command |
| `bin/dashboard_server.py` | Authenticated JSON/Markdown/CSV API | OK | Traversal and unsupported formats must fail closed | Real server happy/adversarial tests pass | None |
| `bin/dashboard.py` | Key artifact panel | OK after fix | Per-key history reload was O(keys × history); ledger-only keys were absent | Isolated ledger-only render test | Reuse one union snapshot; synthesize artifact shells only |
| `README.md` | Command index | OK | None | Docs currency suite | None |
| `docs/user-guide.md` | User workflow and basis semantics | OK | None | Manual contract review | None |
| `docs/architecture.md` | Durable union/statement boundary | OK | None | Cross-file review | None |
| `registry/tests/conftest.py` | Test estate path isolation | OK after fix | Exports initially pointed at operator estate | Isolation pin passes | Redirect `AIQE_EXPORTS_DIR` |
| `registry/tests/test_cost_statement.py` | Arithmetic/export/dashboard coverage | OK after lint fixes | Import ordering and implicit subprocess check mode | Ruff clean | Fixed |
| `registry/tests/test_api_adversarial.py` | Real endpoint behavior | OK | None | Authenticated JSON/CSV + negative cases | None |
| `registry/tests/test_app_paths.py` | Default/relocated export paths | OK | None | Resolver tests pass | None |
| `registry/tests/test_qa_cli.py` | Artifact summary integration | OK | None | Real subprocess assertion | None |

No open P0-P2 per-file defect remains.
