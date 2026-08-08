# Per-file analysis: SDD-S1 vocabulary and state labels

Date: 2026-08-08

| File | Responsibility | Status | Review result |
| --- | --- | --- | --- |
| `engine/lib/glossary.py` | Closed terms, safe markup, state presentation | Pass | Definitions separate meaning/consequence, escape untrusted text, reject unknown ids, and expose internal provenance |
| `bin/dashboard.py` | Journey presentation | Pass | Plain labels precede subordinate machine state; How this works and glossary cards render; `specflow` id and API contract stay unchanged |
| User-facing guides | Newcomer and reference copy | Pass after fix | UI guide, use cases, getting started, and user guide use the new journey name and signed/prose distinction where scoped |
| `registry/tests/test_sdd_usability.py` | S1 acceptance pins | Pass | Bidirectional term coverage, escaping, six real state branches, label order, name/id, ambiguous-word and docs pins |
| `registry/tests/test_event_log.py` | UI/docs currency | Pass | View-id map now follows the visible journey name without changing the machine id |
| `registry/tests/test_standalone.py` | Full mock pipeline | Pass after fix | Retry position is allowed to be posted/updated/skipped while a failed comment still fails |
| `tests/state-adversarial.sh` | State-bundle hostile fixture | Pass after fix | Fixture now carries required manifest metadata and requires preflight checksum refusal |
| Planning/review docs | Delivery control | Pass | S1 acceptance evidence, later dependencies, assumptions, and findings are explicit |

No open P0–P2 S1 code finding remains.
