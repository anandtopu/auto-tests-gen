# PR + JIRA A1 Ticket Discovery — Per-file Analysis

Date: 2026-08-06

## Scope

A1 implementation across feature configuration, queue/API/wizard intake, SCM
and Tracker ports, discovery policy, pipeline wiring, run-record/explain
provenance, mock fixtures, and focused tests. Generated output and unrelated
repository files were excluded.

## Findings

| ID | Severity | File | Line | Finding | Impact | Resolution |
| --- | --- | --- | ---: | --- | --- | --- |
| A1-R1 | P1 | `adapters/tracker/jira.sh` | 22 | A generic curl failure could not distinguish a missing issue from a Tracker outage. | Dead keys and temporarily unvalidated keys could collapse into one state, violating the refusal policy. | Capture the HTTP status; map 404 to exit 3 and all other non-200/transport failures to unavailable. |
| A1-R2 | P2 | `engine/lib/work_queue.py` | 194 | Adding `ticket: null` to every legacy queue entry changed serialized state with the flag off. | Default-off behavior was not representation-compatible. | Write the field only when an explicit ticket is supplied; reads remain backward-compatible. |
| A1-R3 | P2 | `engine/lib/ticket_discovery.py` | 75 | Adapter-provided metadata state was accepted as an open string. | Malformed or hostile port output could create undocumented state names. | Normalize to the closed `available|unavailable` set and bound all metadata fields. |
| A1-R4 | P2 | `catalog/bootstrap/correlate.py` | 6 | Importing the earned key parser executed the bootstrap script against the caller's argv. | Reusing the required grammar crashed discovery or opened unrelated arguments as files. | Guard bootstrap file loading behind `__main__`; cache the imported parser for one extraction. |
| A1-R5 | P2 | `adapters/tracker/jira.sh` | 27 | The first HTTP-status implementation broke existing recording/replay curl wrappers that return JSON on stdout. | Valid ticket parsing and capped comment history failed under a supported test/estate adapter shape. | Accept successful stdout only when it is visibly a JSON object; retain strict status handling otherwise and pin both paths. |

## Per-file conclusion

- Configuration/examples/settings: one default-off flag with consistent naming.
- Queue/dashboard/server: explicit linkage is PR-only, syntactically validated,
  propagated only for that item, and included in dedupe identity.
- SCM adapters: all three real providers emit the same bounded metadata shape;
  mock metadata is deterministic.
- Tracker adapters: validation states are distinguishable without exposing
  response bodies to the discovery artifact.
- Discovery/pipeline: candidate ordering and refusal are deterministic; no key
  can be used before validation.
- Run record/explain: the durable artifact can answer selection and refusal.
- Tests: cover priority, false positives, invalid/unavailable/not-found,
  ambiguity, intake, adapter ports, provenance, and default-off wiring.

No unresolved P0/P1/P2 A1 finding remains.

## Validation

- Focused Ruff: passed.
- Discovery/JIRA regression tests: 22 passed.
- Focused A1/queue/intake/settings/property suite: 93 passed.
- Adapter conformance and modified shell syntax: passed.
- Full registry compatibility suite: 1,423 passed in 702.15s.

## Open questions

The PRD's E1 and E5 remain product decisions; neither blocks the default-off A1
mechanism.
