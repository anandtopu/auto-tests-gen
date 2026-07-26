---
name: test-review
description: Review generated E2E test cases for a PR/JIRA key — the archived diff,
  validation results and advisory critic score — and record findings on the team
  review board. An agent reports; only a named human approves.
triggers: [review tests, test review, test case review, review generated tests]
---
# Test case review (AI-QE agent)

## Steps (all read-only against run state)

1. `python3 bin/qa.py artifacts <KEY> --full` — the plan, scenario table, canonical
   data, generated spec list with create/update actions, validation results, and the
   archived gate diff (the exact committed test code; `workspace/` is ephemeral,
   the diff in `reports/runs/<RUN_ID>-<repo>.diff` is the durable copy).
2. `python3 bin/qa.py critic --findings` — the advisory score and its findings
   (vacuous/weak assertions, duplicates, brittleness). Advisory means advisory:
   a low score is input to YOUR review, never an automatic verdict.
3. `python3 bin/qa.py trace <KEY>` — the full chain (plan → approval → run → gate →
   review → release) if you need provenance.
4. Review the diff like a human reviewer would: assertion quality (status + schema +
   business fields, not snapshots), boundary/negative coverage per AC, no duplicated
   scenarios, stable selectors/clients per the conventions skills.

## Recording the outcome

- Findings that need work:
  `python3 bin/qa.py mark <KEY> changes_requested --by <human> --note "<what and why>"`
- Looks good: report your assessment and leave the key `pending_review` /
  `in_review` for a human to approve — **an agent never marks `approved` on its own
  authority**. If a named human told you to record their approval, pass them via
  `--by`; the review board stores exactly that name.

## Constraints (non-negotiable)

- Read-only on repos: never edit the generated specs during review — a fix goes
  back through `pipeline.sh` so it re-passes the gate.
- The critic score can inform but never decide; the gate results are facts, your
  review is judgment, and the two stay separate.
