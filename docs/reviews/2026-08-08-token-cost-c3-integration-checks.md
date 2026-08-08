# TCA-C3 integration checks

| Boundary | Evidence | Result |
|---|---|---|
| Adapter usage → engine | `provider_usage.retrieve()` is the sole provider source | Pass |
| Provider UTC window → local epoch rows | start inclusive, end exclusive fixtures | Pass |
| Provider identity → ledger identity | only exact same-provider entries participate | Pass |
| Retry TSV → durable ledger → history union → reconciliation | call details survive flush and enriched-record merge | Pass after finding C3-02 |
| Reported dollars → provider dollars | exact Decimal sum and provider-denominated drift | Pass |
| Other bases → scope disclosure | per-basis dollars/calls, call-weighted fraction, no blended total | Pass |
| Unknown/unrecorded → arithmetic | calls counted; absent dollars never become zero | Pass |
| Provider zero → drift | both zero = 0%; disagreement = null percentage plus direction/amount | Pass |
| Legacy history → window precision | multi-attempt aggregate accepted and explicitly disclosed | Pass |
| Reconciliation → live enforcement/history | read-only; no budget mutation or auto-correction | Pass |
| C3 → C4 | no persistence, alarm threshold, notification, maintenance, or UI badge | Pass |

Validation: 33 focused tests, 353 broad relevant tests, Ruff, Python compilation, and
the mock `make cost-reconcile` journey passed. The broad suite left tracked
PROJ-301 artifacts unchanged, confirming the isolation fix from C2.
