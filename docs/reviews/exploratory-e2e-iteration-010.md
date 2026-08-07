# Exploratory E2E Review — Iteration 010

## Scope

This iteration completed Feature 9, Cost, through the real CLI, authenticated
API and served dashboard. It covered all four priced bases plus unknown spend,
workflow/key/phase/provider attribution, turn and cache calibration, artifact
reuse, bounded time filters, malformed persisted records and restart behavior.

## Findings

| ID | Severity | Files | Finding | Impact | Fix |
| --- | --- | --- | --- | --- | --- |
| E2E-EXP-018 | P1 | `bin/dashboard.py` | The API reported unpriced calls but the browser rendered the priced subtotal as an unqualified total. | Operators could read a partial cloud bill as complete and make a false budget claim. | Add a prominent incomplete badge, provider names and an explicit excluded-call statement. |
| E2E-EXP-019 | P2 | `bin/dashboard.py` | Artifact reuse counts and avoided-token bases were present in the report but absent from Cost. | One shipped savings mechanism appeared unused and could not be compared with phase-cache hits. | Render artifact count, avoided tokens and reported/estimated bases without a synthetic dollar value. |
| E2E-EXP-020 | P1 | `engine/lib/cost_report.py`, `bin/dashboard.py`, `engine/lib/trace.py` | Wrong-shaped run, phase or spend fields could abort Cost API reads, dashboard generation and Trace joins. | One corrupt/torn persisted record hid all otherwise valid operational evidence. | Validate and normalize the shared record boundary, skip corrupt rows locally and retain valid evidence. |
| E2E-EXP-021 | P2 | `engine/lib/cost_report.py`, `bin/dashboard_server.py` | Negative windows returned misleading empty data; an enormous integer could close HTTP without a response. | A malformed filter produced a false empty report or apparent service outage. | Bound explicit windows to 1–36,500 days and return 400/CLI exit 2 without harming the next request. |

## Reproduction and retest evidence

- Before E2E-EXP-018, the API returned `unpriced_calls: 1` and
  `unpriced-cloud`, while the browser headline only said `Total $2.2000`.
  Afterward the badge and summary both say the subtotal is incomplete and why.
- Before E2E-EXP-019, the API returned `artifacts_reused: 2` and 1,000 avoided
  tokens but the savings line stopped after phase-cache hits. The browser now
  renders `2`, `1000`, and `(800 reported + 200 estimated)`.
- A numeric synthetic run carrying reported, estimated, local, unknown and mock
  spend proved provider formatting, 1,800 local versus 3,750 cloud tokens,
  phase cache rates, turn calibration and key/workflow attribution.
- Adding a valid JSON record with `phases: null` changed Cost API from 200 to a
  closed connection and made `dashboard.py` abort. After the fix the bad row is
  ignored, the nine valid runs remain, dashboard generation succeeds and Trace
  stays total. A review probe showed malformed inner spend values needed the
  same guard and was added before completion.
- `days=-1` changed from 200/empty to 400; a 1,000-digit window changed from a
  connection failure to 400. `days=1` and the following health request remain
  200. CLI invalid input exits 2 with the valid range.

## Pass 1 — per-file review

- `engine/lib/cost_report.py`: reporting windows are bounded before arithmetic.
  Records require mapping triggers, list phases and finite timestamps. Persisted
  spend fields require string labels, finite non-negative cost and non-negative
  integer usage; corrupt records cannot be priced as zero or crash aggregation.
- `bin/dashboard_server.py`: parse and report validation share one 400 boundary;
  successful data and authentication behavior are unchanged.
- `bin/dashboard.py`: the static run loader applies the same outer shape checks.
  Provider names are HTML-escaped, incomplete totals are conspicuous, and reuse
  details use `textContent` rather than an HTML sink.
- `engine/lib/trace.py`: malformed phase collections are rejected at the shared
  record boundary, closing the residual totality gap from iteration 009.
- Tests pin real-server 400/health behavior, malformed record and spend shapes,
  Cost disclosure/reuse fields, dashboard loader guards and Trace totality.

## Pass 2 — cross-file review

- Correctness: CLI/API/UI use the same report and agree on subtotal, bases,
  tokens, reuse and simulated share. Unknown spend is never converted to zero
  without the incomplete warning.
- Security: untrusted provider labels are escaped in HTML and reuse labels are
  assigned through `textContent`. Oversized query integers are bounded before
  time arithmetic; no file path, secret or customer data is exposed.
- Reliability: corrupt records are isolated rather than poisoning all history;
  dashboard generation, Trace and Cost remained available with the malformed
  seed present and after restart.
- Deployment: no dependency, migration, manifest, state schema or external
  service changed. Old records with absent optional phases/spend still load.
- Coverage: four pre-fix failures pin the discovered behaviors. The final 270
  adjacent tests cover telemetry, budget enforcement, phase cache, artifact
  reuse, providers, spend controls, UI, the real API server and Trace.

## Seed and cleanup review

Both numeric run records were deterministic, synthetic and matched the existing
ignored runtime-record pattern. Mutable queue, OpenHands and generated AGENTS
paths were redirected under ignored `out/exploratory-e2e-iter10`. The two seeds,
browser tab and exact local server processes are removed at iteration end; the
unrelated generated `AGENTS.md` remains unstaged.

## Residual risk

- This estate's organic history is simulated; the measured, estimated, local
  and unknown branches were exercised with synthetic provider records and are
  also covered by provider/budget tests. No real provider spend was incurred.
- Phase-cache dollar savings remain `n/a` because no measured cache-hit median
  exists. That is the required honest state, not a missing calculation.
- The canonical 1,591-test suite is outside the bounded loop; the final 270
  closest checks plus compilation, Ruff and live browser/API/CLI retests passed.
- No blocker remains for Feature 9. Feature 10, Artifacts, is next.
