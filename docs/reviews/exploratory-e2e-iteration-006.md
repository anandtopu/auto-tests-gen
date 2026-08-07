# Exploratory E2E Review — Iteration 006

## Scope

This iteration completed Feature 5, Run progress and Runs & reviews, using four
temporary synthetic run outcomes and isolated review, queue, and provenance
stores. It covered release/review filters, individual and confirmed batch
decisions, persistence across server restart, CLI/API parity, and the prior
retry/stale-state fixes.

## Findings

| ID | Severity | File | Finding | Impact | Fix |
| --- | --- | --- | --- | --- | --- |
| E2E-EXP-011 | P2 | bin/dashboard.py | Individual approval replaced the chip but left the row data-review value pending. | The row remained visible under awaiting review and was missing from approved until reload, so the board contradicted its durable state. | Update the row dataset and reapply the active filters in the same successful transition. |

## Reproduction and retest evidence

- Before the fix, approving EXP-COMMITTED-1 while awaiting-review was selected
  left 2 / 5 rows visible even though its chip said approved. Selecting approved
  then showed only 1 / 5 and omitted that key.
- The focused regression failed before editing because neither dataset update
  nor filter reapplication existed in the approval handler.
- After the fix, the same live flow immediately showed 1 / 5 awaiting and 2 / 5
  approved, including EXP-COMMITTED-1.
- Confirmed batch approval moved two visible pending keys to approved and
  reloaded the board. Restarting the server with the same isolated store still
  showed all three approved synthetic keys.

## Pass 1 — per-file review

- bin/dashboard.py: the change is limited to the successful individual-review
  handler. It derives the owning row before replacing the button, updates only
  data-review after a successful 200 response, and invokes the existing filter
  function. Error behavior still re-enables the original button.
- registry/tests/test_ui_features.py: the focused source invariant pins both
  parts of the UI state transition and documents the user-visible failure.
- docs/exploratory-e2e-status.md: Feature 5 is marked fixed-retested only after
  the missing review and restart scenarios completed.

## Pass 2 — cross-file review

- Correctness: durable review state remains server-authoritative. The DOM update
  happens only after the API succeeds; a failed request cannot relabel or hide
  the row. Release filtering and batch reload behavior are unchanged.
- Security: no authorization, request body, HTML interpolation, secret, or
  external-network path changed. API adversarial checks retained 409 responses
  for unknown/invalid transitions and 400 for malformed JSON.
- Reliability: the update removes the transient split between visible and
  durable state. Restart persistence was verified against the same isolated
  review store, and retry remains rate-limited with one queued record.
- Deployment: this is browser-native dataset/filter logic with no dependency,
  schema, migration, environment, or manifest change.
- Coverage: the focused regression failed before and passed after the fix; 113
  UI, progress, review-state, reviewer-surface, and API adversarial tests passed.
  Python compilation and Ruff E9/F63/F7/F82 checks passed.

## Seed and cleanup review

The four temporary run records and ignored out/exploratory-e2e-iter6 directory
were deterministic, synthetic, PII-free, credential-free, and local-only. They
were removed after browser/API/CLI validation. No shared review, queue,
provenance, production service, or customer record was changed.

## Residual risk

- The regression test is a source invariant rather than a browser test in CI;
  the original behavior was additionally retested in the served browser.
- Full-file Ruff reports 35 existing style findings outside this change. The
  high-signal runtime-error subset passes; broad lint cleanup is deliberately
  deferred to avoid unrelated churn.
- No blocker remains for Feature 5. The next least-covered slice is Feature 6,
  Test plans.
