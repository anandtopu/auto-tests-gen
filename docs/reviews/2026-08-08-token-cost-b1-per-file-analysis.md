# Per-file Analysis: TCA-B1 Complete Consumer Report

Date: 2026-08-08

| File | Responsibility | Local Status | Findings | Test/Validation Gap | Action |
|---|---|---|---|---|---|
| `engine/lib/cost_report.py` | Consumer separation, shared provider/basis rollups, Markdown report | OK after fixes | Probe attribution was dropped; task badge could include probe simulation; unmeterable zero was absent | Mixed task/probe/embedding/unknown fixture | Preserve attribution, partition task totals, always render counts |
| `engine/lib/vector_index.py` | Embedding cap and daily spend ledger | OK after fixes | Scalar days could not expose calls/tokens; RMW and marker writes could race | Legacy upgrade, repeated write and cap-total tests | Backward-compatible structured rows, locked atomic writes |
| `bin/cache-probe.sh` | Real cold/warm provider probe | OK after fixes | Calls bypassed metering/flush; no non-user stamp; no shared lock; custom marker path could retain stale data | Guard/source lifecycle tests and Git Bash syntax | Shared lock, labelled calls, budget record, EXIT flush, `probe` stamp |
| `engine/lib/cost_statement.py` | Exact-key user/non-user partition | OK unchanged | B1 depends on C1's existing partition | Existing mixed-attribution regression | No code change |
| `bin/dashboard.py` | Cost view completeness | OK | New sections were only in JSON/Markdown | UI source contract and generated dashboard tests | Show unmeterable, embedding and probe summaries |
| `registry/tests/test_complete_cost_report.py` | B1 behavioral/adversarial coverage | OK | New coverage required | Five focused cases | Added |
| `registry/tests/conftest.py` | Writable-store isolation | OK after full-suite fix | Vector DB/spend paths initially still resolved into the operator estate | Class-level writer pin | Redirected `AIQE_VECTOR_DB` to test scratch |
| Implementation/user/architecture docs | Acceptance and operating contract | OK | B1 was planned and consumer separation undocumented | Evidence reconciliation | Updated |

No open P0-P2 per-file defect remains.
