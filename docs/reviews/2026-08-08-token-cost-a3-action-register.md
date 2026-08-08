# TCA-A3 Action Register

Date: 2026-08-08

| ID | Severity | Finding | Resolution | Evidence | Status |
| --- | --- | --- | --- | --- | --- |
| A3-01 | P1 | Canonical union lost run-record `turns_used`, breaking turn calibration. | Map `turns_used` to canonical `turns` during run-record normalization. | Targeted cost telemetry passed; full suite 1,720 passed. | Fixed |
| A3-02 | P1 | A malformed spend-bearing record could inflate run totals as metadata-only. | Seed metadata directly only for records with no spend field; spend-bearing records must pass accessor validation. | Wrong-shaped record regression passed; full suite 1,720 passed. | Fixed |
| A3-03 | P2 | Abort-only ledger history could displace a richer completed artifact run in the default CLI view. | Prefer rich records by default; retain abort-only rows for the only-history case and `--all`. | `test_qa_artifacts_view` and adjacent CLI suite passed. | Fixed |
| A3-04 | P3 | The replaced local cost-report normalizer became dead code. | Removed it after accessor migration. | Python compilation and focused suite passed. | Fixed |

Open actions: none for TCA-A3. Product decision Q2 remains scoped to TCA-C1.
