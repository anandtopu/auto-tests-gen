# A6 Learning Loop — Per-file Analysis

Date: 2026-08-06

## Scope

Gate-to-index lifecycle, review/selection outcomes, outcome-aware impact ranking,
run-record evidence, test isolation, and A6 documentation.

## Findings

| ID | Severity | File | Line | Finding | Resolution |
| --- | --- | --- | ---: | --- | --- |
| A6-R1 | P1 | `engine/lib/testcase_learning.py` | 43 | A torn JSONL line could be skipped and then destroyed by the next atomic rewrite, violating append-only provenance. | Strict reads now refuse overwrite; ranking reports the store `unavailable`. Mutation test preserves damaged bytes. |
| A6-R2 | P2 | `engine/lib/testcase_learning.py` | 249 | The gate emits abbreviated SHAs, which are ambiguous outside the checkout and insufficient provenance. | Resolve and verify `^{commit}` to a full SHA before reading files or recording events. |
| A6-R3 | P2 | `engine/lib/testcase_learning.py` | 316 | Summing every review transition lets repeated approval manufacture weight and makes approval after changes-requested cancel to zero. | Ranking derives one signal from the latest decision per key/produced run. |
| A6-R4 | P2 | `engine/lib/impact_analysis.py` | 345 | Outcome history could silently disappear when disabled/corrupt, making ranking behavior unauditable. | Artifacts now record disabled/measured/unavailable plus whether a tie-breaker was applied. |

## Per-file conclusion

- `testcase_learning.py`: safe commit-only reads, narrow upserts, atomic locked
  sidecar writes, bounded fields, idempotent events, and explicit degradation.
- `review_state.py` / `selection.py`: primary human decisions remain authoritative;
  outcome events are emitted after their own store lock is released to avoid
  cross-store deadlocks.
- `impact_analysis.py`: confidence and thresholds are unchanged; outcomes sort
  only equal-confidence candidates and remain feature-gated.
- `pipeline.sh` / `run_record.py`: learning occurs after push-confirmed gate
  results and before run finalization; failure cannot rewrite gate outcome.

No unresolved P0/P1/P2 A6 code finding remains.
