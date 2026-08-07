# A4 Discovery Evaluation — Per-File Review

## Scope

Branch `codex/test-knowledge-a1-a2`; versioned discovery fixtures and labels,
the A4 evaluator, Make targets, scorecard, architecture/user documentation, and
focused tests.

## Findings

| ID | Severity | File | Finding | Resolution |
|---|---|---|---|---|
| A4-R1 | P2 | `eval/discovery_quality.py` | Final decision metrics originally keyed a selection only by ticket key, so two PR fixtures legitimately sharing a ticket collapsed into one sample. | Keyed metric observations by fixture ID plus decision token and added repeated-ticket coverage. |
| A4-R2 | P2 | `eval/discovery_quality.py`, `eval/discovery/v1/labels.json` | The evaluator trusted the editable label file's M1 floor, allowing a label-only change to lower the PRD's fixed 95% gate. | Pinned 0.95 in evaluator code, require the label declaration to match it, and added a weakening regression test. |
| A4-R3 | P2 | `Makefile`, `docs/user-guide.md` | The new `discovery-eval` target had no operator-doc mention; the full suite's documentation currency pin rejected it. | Documented `make discovery-eval` and reran the failing pin. |
| A4-R4 | P3 | `eval/scorecard.py` | The modified scorecard retained legacy multi-import, E402, and semicolon lint violations. | Cleaned imports and statement layout; changed-file Ruff passes. |

No open P0, P1, or P2 finding remains.

## Validation

- Focused and expanded A4/discovery/evaluation tests: 58 passed.
- Integrated `make eval`: passed with M1 precision/recall 1.00, every signal
  precision/recall 1.00, and correct refusal 1/1, labelled simulated.
- Full registry suite: 1,466 passed in 757.39 seconds.
- Changed-file Ruff, fixture JSON parsing, and whitespace checks: passed.

## Open Questions

The synthetic label set proves deterministic policy and plumbing. A QE-owned
real-estate sample is still required before defaulting
`AIQE_PR_TICKET_CONTEXT` on.
