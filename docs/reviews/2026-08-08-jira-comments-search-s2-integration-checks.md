# JCTS-S2 cross-file integration checks

Date: 2026-08-08

## Correctness and contracts

- Dashboard query names map once to the S1 closed contract; neither the browser
  nor HTTP endpoint accepts raw JQL.
- Adapter `{items, returned, total}` is revalidated at the HTTP boundary. The UI
  receives JIRA population counts separately from appended PR rows and confirms
  exactly the returned JIRA page.
- Queue provenance is captured from the validated fetch row and normalized again
  at storage. Missing legacy attributes render as empty values.
- Runtime queue execution remains `(source, key)` only; the pipeline's existing
  `get_item` fetch is still authoritative for mutable ticket fields.

## Security

- Unknown/repeated query parameters and release/fix-version ambiguity fail
  closed. Values flow through S1 adapter escaping rather than string-built JQL.
- Stored arrays and strings have type, count, and length bounds. Validation also
  runs for duplicate queue requests, preventing a dedupe-based bypass.
- UI inserts result content with existing HTML escaping; no credential-bearing
  fields are added to the response or queue record.

## Reliability and deployment

- `AIQE_TICKET_SEARCH=0` is the default in both deployment examples and settings.
  With the flag off, `/api/items` and newly written queue records keep their
  previous shapes.
- Search errors return 502 and render a stale-results warning; a successful empty
  page remains a normal N=0 result. Bulk submission is deliberately sequential,
  and the UI reports non-atomic partial completion.
- Client and server schema versions both moved to 3, preventing silent mixed
  asset/API behavior after deployment.

## Coverage and evidence

- Focused changed-surface run: 100 passed in 70.08 seconds.
- Post-review focused run: 36 passed in 9.67 seconds.
- Broad compatibility run: 325 passed in 128.40 seconds.
- New test file passes full Ruff; modified production files pass Ruff's Python
  syntax/undefined-name rules and Python compilation. Rendered JavaScript passes
  `node --check` through the regression suite.
- Full-file style lint on large legacy production files reports pre-existing
  findings and is not represented as clean; the selected correctness rules and
  new file are clean.

## Residual risk

Bulk queueing is not transactional. This is intentional for B2.3 because each
row must retain normal intake validation; the UI now makes partial completion
explicit. Browser-interactive styling is not automated, but generated markup,
JavaScript parsing, live HTTP behavior, and flag compatibility are covered.
