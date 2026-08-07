# Exploratory E2E Review — Iteration 004

## Scope and evidence

Feature slice: Intake and work queue. The served dashboard at
`127.0.0.1:4999` used mock adapters plus isolated queue/review files. Browser
checks covered release fetch, inline validation and dedupe, plan-only/full
queueing, remove/requeue, failed retry, and a successful drain. Direct API
checks covered empty release behavior and simultaneous drain requests.

Confirmed findings:

| ID | Severity | Reproduction | Fix and retest |
| --- | --- | --- | --- |
| E2E-EXP-005 | P2 | `2026.08` rendered zero items while the adapter exited 127 (`python3` unavailable). | Normalized adapter command/environment; process and JSON failures now fail visibly. Browser fetch returned `PROJ-301`. |
| E2E-EXP-006 | P2 | Remove a plan-only item: queue emptied but fetched row stayed disabled as `Queued`. | Queue mutations refresh an open fetched-results card. Original browser path restored both actions immediately. |
| E2E-EXP-007 | P2 | Send two concurrent `POST /api/queue/run` requests: both returned 200. | Atomic nonblocking lock acquisition before thread launch; retest returned 200/409. |

## Pass 1 — per-file review

- `bin/dashboard_server.py`: command arguments remain structured through
  `git_bash_command`, the running interpreter directory is prepended, invalid
  adapter results no longer masquerade as domain emptiness, and lock ownership
  is released on worker completion or thread-launch failure. No credential or
  production endpoint was introduced.
- `bin/dashboard.py`: fetched-item refresh is reused by the button and mutation
  paths. The additional request is conditional on the card being open, avoiding
  background adapter churn when release results were never requested.
- `registry/tests/test_work_queue.py`: tests pin runtime normalization and
  fail-visible adapter behavior without launching real services.
- `registry/tests/test_run_progress.py`: tests pin fetched-state coherence and
  lock acquisition/release ordering; live E2E evidence covers the concurrency
  behavior that the source-level invariant protects.
- `docs/exploratory-e2e-status.md`: records only observed outcomes and synthetic
  seed provenance.

## Pass 2 — cross-file review

- Correctness: release-row eligibility and persisted queue state now converge
  after mutations; known-empty releases remain valid empty lists while adapter
  faults are distinct 502 responses.
- Security: release and key values stay positional arguments across the existing
  quoted Git Bash boundary. Seeds contain no secrets or PII, and isolated files
  were removed. No authorization behavior was weakened.
- Reliability: the drain lock closes the check/acquire race and is not stranded
  if worker startup or execution fails. Failed items preserve attempt and prior
  error metadata across requeue.
- Deployment: changes use existing Python, threading, subprocess, and browser
  primitives; no dependency, schema, migration, or production configuration
  change is required. Windows runtime behavior was directly exercised.
- Coverage: targeted regressions cover all three fixes; adjacent queue, dashboard,
  adversarial, and syntax/lint checks are recorded in the iteration commit
  evidence. Residual risk is limited to external non-mock tracker/SCM service
  behavior, which was intentionally not contacted.

No additional actionable P0–P2 finding remained after the two review passes.
