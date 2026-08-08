# Review Action Register: SDD-S2 journey actions and refusal contracts

Date: 2026-08-08

| ID | Severity | Status | Owner Area | Finding | Evidence | Recommended Action | Validation Expected | Dependencies |
|---|---|---|---|---|---|---|---|---|
| S2-01 | P1 | Completed | Drift reliability | A stale scenario could reference a newly vanished surface without changing its id, suppressing evidence update and notification | `spec_drift.py` change predicate | Compare/persist surface maps as well as ids | Same-id/new-surface test sends a second exact contract | None |
| S2-02 | P1 | Completed | Gate correctness | Warn mode inherited “Delivery refused” even though it commits | Captured `spec_check.main` output | Construct refusal contracts only for strict mode | Warn omits refusal; strict includes it and returns 8 | None |
| S2-03 | P2 | Completed | API integrity | Durable entry keys and malformed legacy maps could override or break computed refusal evidence | `/api/plans/one` response composition | Put canonical fields last; type-check the map | API/adversarial suite passes | None |
| S2-04 | P2 | Deferred | Visual verification | In-app browser runtime assets remain unavailable | Prior S1 browser initialization failure | Repeat visual click-through when runtime is repaired | Six action buttons clicked across seeded states | Browser runtime repair |

## Status Summary

| Status | Count |
|---|---:|
| Open | 0 |
| In Progress | 0 |
| Blocked | 0 |
| Ready for Verification | 0 |
| Completed | 3 |
| Deferred | 1 |
