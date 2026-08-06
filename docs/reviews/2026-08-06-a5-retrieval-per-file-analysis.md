# A5 Retrieval Quality — Per-file Analysis

Date: 2026-08-06

| File | Review result |
| --- | --- |
| `eval/retrieval_quality.py` | Uses production A3 rankers and separate mode metrics. Fixed fail-closed validation for non-object rows and invalid embedding vectors. |
| `eval/retrieval/v1/` | Thirty balanced, QE-owned labels pin a stable corpus hash. Hostile content is data-only; M9 is honestly `unmeasured`. |
| `Makefile`, `eval/scorecard.py`, `registry/org-config.yaml` | Eval/review wiring, schema-based scorecard discovery, and bounded per-mode floors are coherent. |
| `registry/tests/test_retrieval_quality.py` | Covers math, drift/traversal, mode isolation, regression/outage, vector validation, attack mutation, M9, and wiring. |

Resolved findings: malformed labels now fail as fixture errors; heterogeneous or
non-finite vectors cannot produce plausible cosine scores; mock semantic results
are `simulated` and non-gating. No unresolved P1/P2 code finding remains.
