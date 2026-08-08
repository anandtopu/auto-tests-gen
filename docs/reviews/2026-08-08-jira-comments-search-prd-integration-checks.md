# JIRA comments/search PRD — cross-file integration checks

## Pass 2 scope

The second pass traced structured search from caller input through both Tracker
adapters and their compatibility consumer, then traced the planned queue and
comment flows across their state and visibility boundaries.

## Search data and control flow

1. Callers supply a JSON object, never JQL.
2. `ticket_search.normalize_filters` admits only the six named string filters.
3. The Jira adapter owns JQL composition; the mock adapter applies the same
   normalized filters to synthetic fixtures.
4. New `search` returns `{items, returned, total}`. Existing
   `search_release` projects `items` back to its legacy list contract, so the
   current dashboard remains compatible until S2.
5. Shared processed names originate in `ticket_fields`; search adds only the
   discovery-only status and text fields.

Confirmed integration findings:

- **JCTS-01 (P1, fixed in S1):** raw release interpolation crossed the
  caller/adapter trust boundary. The shared builder now fixes field/operator
  selection and escapes values as JQL string literals.
- **JCTS-02 (P1, fixed in S1):** Jira and mock previously exposed different
  discovery capability. Both now implement the same six-filter contract and
  output projection.
- **JCTS-03 (P2, fixed in S1):** page length could be mistaken for population.
  The envelope preserves Jira `total` and mock computes total before truncation.

## Security boundaries

- Unknown keys and non-string filter values fail closed with exit 64. There is
  no raw-JQL escape hatch and no input-controlled field or operator.
- The named malicious release (`x" OR key in (SEC-1)//`) reaches Jira as one
  escaped literal and mock as one unmatched literal; it never becomes control.
- Ticket/filter/comment text remains data. Later S5 markers are identifiers,
  not update authorization: authorship verification is still mandatory.
- Rich comments may expose only existing run/PR projections, repo-relative test
  paths, and basis-labeled cost states—never prompts, absolute paths, or secrets.

## Queue and runtime authority

S2 may persist `issue_type`, `components`, `labels`, and `fix_version` for
display/filter provenance. Cross-file correctness requires `work_queue.run_all`
to continue passing only the workflow identity into `pipeline.sh`; pipeline
startup must refetch `get_item`. Legacy queue rows must render absent fields as
empty. Bulk queue must reuse individual intake validation and show both page and
population counts.

## Comment state and visibility

- S3 records every attempt without making comments fatal. Run-mode attempts
  belong in the run record and event log; plan-mode attempts belong in
  `plan_state` plus the event log because plan mode has no run record.
- S4 must extract one delivery projection from `pr_comment`, not create a
  parallel formatter. Plain text is the compatibility floor.
- S5 persists comment ids in the locations defined by S3, compares bodies before
  writes, verifies platform authorship before updates, and appends with explicit
  supersession evidence when update is unavailable or unsafe.

## Deployment and rollback

- S1 is additive except for the unconditional security fix. Rolling back the
  new verb must never restore raw interpolation.
- S2 and S4 remain independently default-off behind their PRD flags.
- S3/S5 are schema-compatible enhancements to existing best-effort delivery;
  absent blocks must preserve legacy reads.
- Required gates are Python behavior tests, real-adapter curl stub, mock adapter
  journey, Bash syntax, adapter conformance, Ruff, adjacent dashboard/queue tests,
  and the broad registry suite.

## Residual risks

ADF/wiki rendering, mentions, follow-up comments, and saved presets remain the
PRD's explicit later product decisions. They do not weaken the S1 plain-text,
structured-filter, ownership, accounting, or pagination requirements.
