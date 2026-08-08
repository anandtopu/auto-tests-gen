# TCA-C3 action register

| ID | Priority | Finding | Resolution | Status |
|---|---:|---|---|---|
| C3-01 | P1 | A consolidated retry stored only its earliest timestamp, so calls straddling UTC midnight could be assigned to the wrong provider bucket. | Preserve optional call-level timestamp/provider/basis/cost evidence inside the durable aggregate and use it for window filtering. | Fixed |
| C3-02 | P1 | The run-record-wins union overwrote ledger `attempt_details` with the run record's normalized empty list. | Explicitly preserve ledger attempt evidence during `_merge`; regression spans both sources. | Fixed |
| C3-03 | P1 | The initial unavailable path skipped normalized-contract validation, so a poisoned unavailable response could carry a zero-like cost. | Validate every provider payload before branching on state. | Fixed |
| C3-04 | P2 | Quantizing a very large valid Decimal under the default context could raise. | Render exact Decimal evidence with fixed-point formatting; adversarial large-value test added. | Fixed |
| C3-05 | P2 | A dollar-weighted reconcilable fraction would require blending reported, estimated, simulated, and local bases. | Use call-attempt weighting, name the denominator, and keep dollar evidence strictly partitioned. | Fixed |
| C3-06 | P2 | Historical multi-attempt aggregates cannot be split exactly at a UTC boundary. | Continue reading them once, mark `window_precision=legacy-aggregate`, and count affected rows. New entries are exact. | Residual |

Next item: TCA-C4 reconciliation operations.
