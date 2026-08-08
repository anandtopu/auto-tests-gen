# Review Action Register: TCA-B1 Complete Consumer Report

Date: 2026-08-08

| ID | Severity | Status | Owner Area | Finding | Evidence | Action | Validation |
|---|---|---|---|---|---|---|---|
| TCA-B1-R1 | P1 | Completed | Accounting | Real cache-probe calls had no live record or durable flush | Original direct-phase flow | Record both labels and EXIT-flush normal ledger | Lifecycle source test |
| TCA-B1-R2 | P1 | Completed | Attribution | Probe spend could inflate a user's total | Collected model dropped A1.7 stamp | Preserve attribution and partition non-user rows | Mixed fixture |
| TCA-B1-R3 | P1 | Completed | Honesty | Unknown/OpenHands tasks had no explicit count | Existing report banner | Add always-present phase/task/provider line | Unknown + zero cases |
| TCA-B1-R4 | P2 | Completed | Reliability | Probe shared scratch without lock and could retain custom markers | Shell lifecycle review | Lock; clear configured ledger/marker path | Syntax + guard tests |
| TCA-B1-R5 | P2 | Completed | State integrity | Embedding and marker RMW could clobber updates | Store review | Lock plus atomic replacement | Repeated-write test |
| TCA-B1-R6 | P2 | Completed | Reporting | Probe activity could change the user measured/simulated badge | Rollup trace | Count share only after user gate | Compatibility fixture |
| TCA-B1-R7 | P2 | Completed | Compatibility | Legacy embedding days lack call/token evidence | Existing schema | Preserve dollars; report counts unknown | Legacy test |
| TCA-B1-R8 | P1 | Completed | Test isolation | Locked embedding writers resolved into the operator estate during the full suite | Estate-writer pin failed after 1,738 passes | Redirect `AIQE_VECTOR_DB`; its sibling spend path follows automatically | Isolation pin + full rerun |

Open: 0 · Completed: 8 · Deferred: 0.
