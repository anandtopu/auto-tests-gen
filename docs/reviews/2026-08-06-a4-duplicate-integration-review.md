# A4 Near-Duplicate Detection — Cross-File Integration Review

Date: 2026-08-06

## End-to-end flow

`testplan/generate contract → duplicate detector → plan state or run record → plan editor or PR comment → optional typed selection outcome`

The detector is evidence-only. The generation contract, generated files,
validation contract, and gate result are never inputs that it can mutate.

## Pass results

| Pass | Result |
| --- | --- |
| Correctness | JIRA and PR timing matches when each workflow first has a complete proposal. Warning provenance survives scratch cleanup. M6 counts distinct recorded warnings/exclusions as stored. |
| Security | Ticket, scenario, and testcase text remain data. Raw queries are not persisted; UI output is HTML-escaped and PR Markdown is bounded/sanitized. The detector has no repository-write or network authority beyond the optional embedding adapter. |
| Reliability | Missing/malformed optional artifacts are total; disabled mode removes stale advice; embedding outage falls back to lexical; detector failure is logged and cannot stop the pipeline. |
| Deployment | Default behavior is unchanged because `AIQE_ARTIFACT_REUSE=0`. Controls are exposed in `.env`, properties, and Settings with independent thresholds. No schema migration is required; readers tolerate absence. |
| Test coverage | Focused tests exercise positive, negative, outage, threshold, bounds, stale-state, archival, presentation, and selection-audit paths. Broad registry compatibility is run before commit. |

## Findings fixed during review

1. **High — M6 denominator counted object keys, not warnings.** Selection status
   used `len(artifact)` after plan state moved from a list to a versioned object.
   It now reads `warning_count` with a bounded fallback.
2. **Medium — embedding outage erased advisory coverage.** A configured provider
   throwing during query caused the entire optional detector to fail. It now
   degrades to lexical scoring and a regression test pins the behavior.
3. **Low — presentations omitted suite provenance.** Both plan editor and PR
   comment now include the case suite as required by the implementation plan.
4. **Low — corrupt metric metadata could break selection status.** Count parsing
   is total and falls back to the warning list rather than raising.

## Residual constraints

- Retrieval precision/recall and threshold fitness are intentionally unclaimed
  until A5 supplies the labelled measurement harness.
- The feature remains default-off; enabling it before A5 is an operator preview,
  not evidence that the configured thresholds meet product quality targets.
- This Windows host cannot execute the repository shell pipeline directly
  without the test harness's bash bridge; Python integration tests cover the
  wiring, and the limitation is retained in the iteration record.
