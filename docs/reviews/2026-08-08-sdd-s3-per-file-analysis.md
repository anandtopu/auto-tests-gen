# Per-file analysis: SDD-S3 adoption levels

Date: 2026-08-08

| File | Responsibility | Review result | Finding / resolution |
| --- | --- | --- | --- |
| `engine/lib/adoption_levels.py` | Four names, consequences, resolved derivation, and closed writes | Ready | Mapping is limited to three existing controls; warn/strict remain distinct; invalid input is typed and closed |
| `engine/lib/spec_workflow.py` | Effective engine truth | Ready after fix | Resolver output is authoritative; ignored invalid values stay visible on repeated refreshes and force Custom |
| `engine/lib/plan_state.py` | Requirements-gate resolver | Ready after fix | Optional warning sink exposes unusable configuration without changing existing callers or gate semantics |
| `engine/lib/settings_store.py`, `.env.example` | Existing atomic `.env` store and advanced controls | Ready | Added the existing spec-mode knob; no new behavior control or persistence store |
| `bin/dashboard_server.py` | Authenticated read/apply boundary | Ready after fixes | Applies one complete closed update atomically, refreshes resolved truth, catches type/value errors as 400, and logs keys but no values |
| `bin/dashboard.py` | Settings, Start here, and Governance presentation | Ready | All names/consequences come from the definitions/resolved API; Custom exposes escaped raw values; warn has an explicit dry-run badge |
| `engine/lib/governance_page.py` | Generated/shareable governance | Ready | Uses the same adoption object while retaining internal constitution clauses |
| `registry/tests/test_sdd_usability.py` | Mapping and presentation pins | Ready | Exact names, five tuples, Custom, repeated invalid values, three-key mutation pin, and shared-source assertions |
| `registry/tests/test_api_adversarial.py` | Real authenticated boundary | Ready | Unauthorized, malformed, numeric, exact-write, live-refresh, warn badge, and restore paths exercised against an isolated server |
| User/architecture/plan docs | Operator contract and status | Ready | Four levels, Q1/Q4 assumptions, precedence, Custom truth, and validation limits recorded |

No machine state, path, Make target, gate decision, plan signature, or workflow
transition changed in SDD-S3.
