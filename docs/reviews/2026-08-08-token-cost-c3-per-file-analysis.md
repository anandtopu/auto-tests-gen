# TCA-C3 per-file analysis

Scope: C2.1b/C2.2 arithmetic and evidence only. Persistence, threshold policy,
Notify delivery, maintenance, and Cost-view states remain TCA-C4.

| File/surface | Review result |
|---|---|
| `engine/lib/cost_reconcile.py` | Pass after fixes. Pure same-provider, UTC-window, reported-only Decimal comparison; basis-separated evidence; no vendor, persistence, notification, threshold, or correction branch. |
| `engine/lib/spend_ledger.py` | Pass. Optional per-attempt evidence extends schema 1 compatibly and does not change phase aggregation or live enforcement. |
| `engine/lib/spend_history.py` | Pass after fixing merge precedence. Normalization filters unsafe details and the enriched run record no longer erases ledger call evidence. |
| `Makefile` | Pass. Existing `cost-reconcile` target now invokes arithmetic rather than stopping at raw provider evidence. |
| Focused tests | Pass. Cover inclusive/exclusive boundaries, provider scoping, every basis, exact and legacy retries, zero denominator, unavailable/poisoned input, large Decimal values, and port isolation. |
| Product/architecture docs | Pass. Explain call-weighted fraction, separate dollar bases, no correction, and the C3/C4 boundary. |

The generated `AGENTS.md` timestamp remains unrelated and excluded.
