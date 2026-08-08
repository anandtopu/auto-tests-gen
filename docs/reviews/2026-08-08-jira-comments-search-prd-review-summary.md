# JIRA comments/search PRD — review summary

## Outcome

Ready to build in the PRD's S1–S5 order. Draft v2 is implementable: it resolves
comment-id persistence, plan-mode receipt storage, pagination truthfulness,
comment ownership checks, and the plain-text compatibility floor.

The interrupted run was recoverable. No unpushed commit existed; the code and
tests for S1 were intact. The crash interrupted creation of three review files,
which have been reconstructed from the completed per-file and integration review.

## Findings by severity

- **P1 JCTS-01:** current release search had live JQL injection through direct
  interpolation. S1 replaces it with a closed structured builder and exact
  malicious-value tests on both adapters.
- **P1 JCTS-02:** the Tracker port and mock could not support the PRD's six-field
  discovery path. S1 adds one normalized contract while retaining the legacy
  `search_release` response for current dashboard consumers.
- **P1 JCTS-05/06/07:** comment attempts, plan-mode receipts, and update ownership
  require the S3/S5 state design exactly as written; shortcuts would either hide
  failures or permit edits to human comments.
- **P2 JCTS-03/04/08:** field vocabulary, page/population counts, queue provenance,
  and delivery projection must remain shared across their consumers.

## Architecture and compatibility

The Tracker port remains the sole Jira boundary; no raw JQL enters engine or UI
code. `search` returns a truthful envelope and `search_release` remains a
list-shaped compatibility wrapper. Queue attributes remain display provenance,
never runtime authority. Comment accounting precedes rich content, and existing
best-effort behavior remains while failures become visible facts.

## S1 recovery validation

The recovered targeted suite passed 42 tests covering all six filters, AND
semantics, Jira/mock injection handling, page totals, result attributes, legacy
release-search shape, shared ticket fields, and adjacent queue/dashboard behavior.
Bash syntax, adapter conformance, Ruff, and Python compilation passed. The broad
changed-surface set passed 265 tests across adapter HTTP behavior, API adversarial
cases, intake, ticket comments/discovery, UI contracts, and queue behavior. The
all-registry command timed out after 904 seconds without a result; it is not
counted as passing and remains a final-suite coverage residual.

## Residual decisions and next action

Optional ADF/wiki rendering, mentions, follow-up comments, and saved presets are
later product decisions and do not block S1. Commit and push the completed S1
slice, verify remote parity, then advance the loop to S2.
