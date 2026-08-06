# A5 Retrieval Quality — Cross-file Integration Review

Date: 2026-08-06

## Correctness and compatibility

- The fixture's canonical surfaces match A3 normalization; deterministic and
  lexical scores exercise production ranking code rather than a duplicate model.
- `make eval` orders retrieval evaluation before the scorecard, and scorecard
  routing discovery uses the `routing_ok` schema field so the new result cannot
  be misparsed as a routing fixture.
- Label and corpus hashes, evaluation timestamp, and source commit make result
  currency auditable. Corpus drift fails until labels are deliberately repinned.

## Security, reliability, and deployment

- Sibling-only fixture references prevent traversal outside the version folder.
- The hostile fixture is framed by the production context preamble and is never
  read by the deterministic gate. Weakening framing makes the oracle fail.
- Configured semantic outages and malformed vectors fail; unconfigured semantic
  service records `unmeasured` without failing lexical/deterministic evaluation.
- No runtime service or migration is introduced. The new result is derived and
  gitignored; all durable source fixtures are version controlled.

## Coverage conclusion

Focused A5/A3/context/integrity tests cover both positive paths and adversarial
mutations. Broad registry validation is the compatibility gate for this change.
The only residual measurement gap is M9 human time-to-first-relevant-result: it
cannot be populated until a real QE survey is collected, and remains visibly
`unmeasured` rather than blocking executable A5 acceptance criteria.

## Validation

- Focused A5/A3/context/integrity: 44 passed.
- Full registry suite with the repository's mock adapters: 1,333 passed.
- Non-mock diagnostic: 1,329 passed and four mock-fixture-dependent tests failed;
  all four passed when rerun with `AIQE_MOCK=1`.
- Retrieval evaluator: deterministic 1.0000/1.0000/1.0000 and lexical
  0.9000/0.9000/0.9667 for precision@5/recall@5/MRR; semantic mock result is
  separately labelled `simulated` and non-gating.
