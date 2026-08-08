# Integration Checks: TCA-B1 Complete Consumer Report

Date: 2026-08-08

## End-to-end paths traced

1. User phase: provider start marker → normalized result → live `budget.py`
   ledger → durable EXIT flush → `spend_rows()` → user task total and shared
   provider/basis rollups.
2. Cache probe: shared run lock → labelled cold/warm starts → two
   `budget.py record` calls → EXIT flush with `probe` attribution → Probe and
   statement non-user sections, outside ticket totals.
3. Embedding refresh: existing pre-call daily cap → embedding adapter → locked
   daily spend update → read-only report normalization → Embedding section and
   the same rollups. No second enforcement point was introduced.
4. Unknown provider: durable `unknown` row → incomplete banner plus mandatory
   phase/task/provider count; no synthetic dollar value.

## Cross-cutting checks

| Area | Result | Evidence |
|---|---|---|
| Correctness | Pass | Task total excludes probe/embedding; shared rollups count each row once |
| Security | Pass | UI escapes dynamic basis strings; no credential or vendor API added |
| Reliability | Pass after fixes | Probe shares lock and flushes on failure; embedding RMW is locked/atomic |
| Deployment | Pass after fix | No service/package; vector DB and spend ledger are redirected under tests; legacy ledger upgrades in place |
| Compatibility | Pass with recorded transient | 110 focused/adjacent; 199 compatibility; two full sweeps at 1,738 pass each; unrelated plan-arbitration failure passed alone |
| Coverage | Pass | Structured/legacy embeddings, probe exclusion, unknown/zero counts and UI wiring covered |

## Residual risks

- Historical scalar embedding days contain no recoverable call/token count;
  those fields remain `unknown` rather than being invented.
- A real `make cache-probe` was not executed because it intentionally bills a
  provider and requires operator credentials. Its lifecycle uses already-tested
  start, meter and durable-flush components.
- The full suite's multi-agent plan-arbitration test was green in the first
  sweep, failed in the second, and passed immediately alone; no B1 path or file
  participates in that mock scenario.
