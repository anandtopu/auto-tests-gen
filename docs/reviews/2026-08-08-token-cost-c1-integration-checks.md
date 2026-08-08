# Cross-file Integration Checks: TCA-C1 Per-task Cost Statement

Date: 2026-08-08

## Flow Checks

| Flow | Files Checked | Status | Evidence | Gap/Risk | Action |
|---|---|---|---|---|---|
| Durable history -> exact-key statement | `spend_history.py`, `cost_statement.py` | Pass | Prefix-neighbor key excluded; retries retain attempts | None | None |
| Basis rows -> totals/renderers | `cost_statement.py`, tests | Pass | No blended total field; seven explicit state classes plus incomplete priced count | Missing-cost row first read as numeric zero | Fixed |
| Make/CLI -> exports | `Makefile`, `qa.py`, `cost_statement.py`, `app_paths.py` | Pass | Real CSV export; locked atomic replace; deterministic rerun | First broad suite found operator-estate path | Added relocatable/test-isolated path |
| API -> JSON/Markdown/CSV | `dashboard_server.py`, `cost_statement.py` | Pass | Authenticated server tests; bad key/format 400 | None | None |
| Ledger-only key -> artifact panel | `dashboard.py`, `spend_history.py`, `cost_statement.py` | Pass | Isolated plan ledger renders key and basis summary | Must not alter run metrics | Synthetic shell stays outside `runs` |
| Artifacts CLI -> statement drill-down | `qa.py`, test | Pass | Compact per-basis summary and dedicated command | Initial output obscured artifacts | Fixed |

## Contract Checks

| Contract | Producer | Consumer | Status | Notes |
|---|---|---|---|---|
| Canonical spend row | `spend_history.py` | `cost_statement.py` | Pass | Reads only accessor; no vendor/source resolution |
| Exact task identity | Caller key | model/API/dashboard | Pass | 1–128 safe characters; exact equality; no inference |
| Export row schema | statement model | Finance CSV/Markdown | Pass | One line per phase; non-user rows retained and attributed |
| Mutable export path | `app_paths.exports_dir()` | writer/tests | Pass | Default unchanged; state and direct env relocation supported |
| Metrics boundary | run records | dashboard metrics | Pass | Ledger-only shells exist only in artifact rendering |

## Integration Findings

- **TCA-C1-R1 (P1, fixed):** missing numeric cost on a priced basis could read
  as zero; it now increments `incomplete_priced_rows`.
- **TCA-C1-R2 (P1, fixed):** the new export writer escaped the test estate; a
  centralized relocatable export path and conftest redirect close the leak.
- **TCA-C1-R3 (P2, fixed):** dashboard panels reread all history per key; one
  snapshot now serves all panels.
- **TCA-C1-R4 (P2, fixed):** artifact CLI detail overwhelmed the core artifact
  view; it now renders a compact summary with a drill-down command.
