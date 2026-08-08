# TCA-C4 per-file analysis

Scope: C2.3/C2.4/M4 reconciliation operations. Broad final PRD verification
remains TCA-FINAL. The generated `AGENTS.md` timestamp is unrelated and excluded.

| File/surface | Review result |
|---|---|
| `engine/lib/cost_reconcile.py` | Pass after fixes. Exact threshold semantics, atomic relocatable state, three states, fail-closed reads, Notify-port delivery, distinct external exit 75, and no correction or vendor branch. |
| `engine/lib/maintenance.py` | Pass after fixes. Runs reconciliation nightly and degrades only exit 75; exit 1 remains a failing local fault. Summary no longer claims a partially completed external check never ran. |
| `bin/dashboard_server.py` | Pass. Cost payload adds read-only reconciliation state; missing/corrupt state cannot break polling or become green. |
| `bin/dashboard.py` | Pass after fix. One dedicated badge renders exactly three labels; tooltip assignment uses the safe DOM property without HTML escaping artifacts. |
| `registry/org-config.yaml` | Pass. Explicit 10% default under existing budgets policy; non-object/negative/non-finite values fail locally. |
| `.gitignore` and state lifecycle | Pass. Named runtime state is ignored, relocates with `AIQE_COSTS_DIR`, is bundled/reset with `reports/costs`, and is skipped by history/prune readers. |
| Tests and docs | Pass. Cover every state, boundary and undefined percentage, delivery failure, adapter timeout, local/external maintenance classification, API shape, and source isolation. |
