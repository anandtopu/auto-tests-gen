# PR + JIRA A1 Ticket Discovery — Cross-file Integration Review

Date: 2026-08-06

## Scope

End-to-end control and data flow from PR intake through queue execution, SCM
metadata, Tracker validation, deterministic selection/refusal, prompt context,
run-record persistence, comments, settings, and explainability.

## Findings

| ID | Severity | Boundary | Finding | Impact | Resolution |
| --- | --- | --- | --- | --- | --- |
| A1-I1 | P1 | SCM → discovery → Tracker | A plausible key must never cross into context solely because it matches the grammar. | A wrong ticket can poison every generated assertion. | Every candidate is validated; ambiguity and validation outage proceed without a ticket. |
| A1-I2 | P2 | Settings → pipeline → phase argv | Default-off parity requires more than hiding the UI field. | Extra port calls/files/arguments can alter existing PR runs. | The flag gates the full discovery block and the context array expands to zero arguments while off. |
| A1-I3 | P2 | Queue → runner environment | A process-global explicit key could leak between queued PR items. | One PR could inherit another PR's ticket. | `run_all` builds a fresh item environment and exports the key only when that queue item carries it. |
| A1-I4 | P2 | Discovery → run record → explain | Outcome alone is insufficient evidence for later audits. | Operators cannot answer why a ticket was cited or refused. | Candidate signals, validation states, selected/rejected keys, rule, and reason persist together and render in explain. |

## Correctness, security, and reliability

- Metadata text is bounded before parsing; only grammar-approved keys become
  filesystem suffixes or Tracker arguments.
- Ticket/PR text is never executed or treated as policy. A1 passes only its
  explicit discovery-state summary; ticket body fusion remains A2.
- Temporary response files are removed by adapter traps. The pipeline replaces
  the resolved artifact atomically within the single run scratch directory.
- Tracker unavailability and malformed metadata degrade visibly without failing
  the existing PR generation path.
- Ambiguity names validated candidates in the PR comment and requires explicit
  intake to resolve; it never guesses.

## Test coverage

Focused tests exercise the policy as a pure function and cross the queue,
adapter, run-record, explain, settings, property, and pipeline/UI boundaries.
The broad registry suite remains the compatibility gate. Live GitHub,
Bitbucket, Stash, and JIRA calls are not made in unit tests; their shell syntax
and conformance contracts are validated locally.

## Validation

- Focused A1/queue/intake/settings/property suite: 93 passed.
- Discovery plus existing JIRA description/comment regressions: 22 passed.
- Full registry suite: 1,423 passed in 702.15s.
- Ruff on new discovery source/tests: passed.
- Adapter conformance: passed.
- Git Bash syntax on pipeline and all modified adapters: passed.

## Open questions

- Per-provider commit pagination beyond the first 100 messages is deferred to
  measured E1 needs; branch/title signals and explicit intake remain available.
- A1 comments only ambiguity as required. General ticket-link comments await E5.
