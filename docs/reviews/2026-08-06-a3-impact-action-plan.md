# A3 Impact Analysis — Review Action Plan

Date: 2026-08-06

| ID | Priority | Status | Action | Validation |
| --- | --- | --- | --- | --- |
| A3-R1 | P1 | Fixed | Do not classify a surface moved within a diff as `replace` | Added moved-surface regression test |
| A3-R2 | P1 | Fixed | Never use a different live run's artifact for historical explain | Added cross-run explain regression test |
| A3-R3 | P2 | Fixed | Preserve ranked bug surface evidence when a threshold forces fallback | Added above-surface-threshold bug test |
| A3-R4 | P2 | Fixed | Harden optional health/chunk/catalog/score inputs and empty queries | Malformed-health and zero-call tests |
| A3-R5 | P2 | Fixed | Make retrieval trust and gate authority explicit | Prompt and artifact boundary assertions |
| A3-R6 | P2 | Fixed | Resolve CI health through the state/catalog path abstraction | Catalog relocation conformance test |
| A3-R7 | P2 | Fixed | Keep both supported config examples conformant with Settings SPEC | Properties/settings conformance tests |
| A3-R8 | P2 | Deferred to A5 | Measure/tune per-mode retrieval quality before default enablement | Labelled precision@5, recall@5, MRR harness |

Release recommendation: A3 is acceptable behind `AIQE_IMPACT_ANALYSIS=0`.
Do not enable it by default until A3-R8 is complete.
