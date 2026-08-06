# A3 Impact Analysis — Pass 1 Per-file Review

Date: 2026-08-06
Scope: PRD A3 implementation only

| File | Role | Review result | Finding / resolution |
| --- | --- | --- | --- |
| `engine/lib/impact_analysis.py` | Versioned deterministic-first ranker | Pass after fixes | Fixed moved surfaces being mislabeled `replace`; retained bug surface candidates across fallback; bounded/deduplicated cases; rejected malformed rows/non-finite scores; health remains tie-break only and resolves through relocated-state `app_paths`; empty queries spend no embedding call; atomic artifact replace added. |
| `engine/pipeline.sh` | PR/JIRA/tests/plan lifecycle hooks | Pass | Hooks run after catalog preparation and final scenario arbitration. Default-off yields zero context arguments; enabled failures cannot masquerade as empty success. |
| `prompts/pr-generate.md` | Authoring interpretation | Pass | Added explicit untrusted-data boundary, threshold semantics, unaffected handling, and proposal-only authority. |
| `engine/lib/run_record.py` | Durable evidence | Pass | Valid A3 artifacts are archived; missing/torn optional JSON cannot destroy the run record. |
| `engine/lib/explain.py` | Human-readable rationale | Pass after fix | Fixed historical records borrowing another live run's artifact; reasons, active mode/threshold, explicit none, and bug result are evidence-backed. |
| `engine/lib/settings_store.py`, `.env.example`, `aiqe.properties.example` | Default-off deployment controls | Pass after fix | Flag and deterministic/semantic/lexical thresholds are documented and editable in both supported configuration formats; semantic and lexical distributions are not conflated. The broader conformance run also exposed and closed the pre-existing omission of A1's two settings from the properties example. |
| Focused tests | Contract and regression evidence | Pass | PR/JIRA, bug, zero-embedding, semantic/lexical, threshold, bounded none, moved surface, malformed state, pipeline, archive, and explain paths covered. |

No unresolved P0/P1/P2 finding remains in the reviewed A3 files.
