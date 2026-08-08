# TCA-C3 review summary

## Outcome

Ready to commit. Reconciliation now compares provider evidence with the exact
same-provider UTC window and only `reported` platform dollars. The arithmetic
is deterministic, basis-honest, read-only, and vendor-independent.

## Correctness and reliability

- Provider `[start, end)` timestamps govern filtering; local time zones do not.
- Each new provider call has call-level durable evidence even when phase retries
  remain consolidated under the established history identity.
- Drift includes exact absolute dollars, provider-denominated percentage, and
  explicit under/over direction. A zero provider denominator is not faked.
- Unknown/unrecorded evidence contributes to the disclosed call denominator but
  never to dollars. Every monetary basis remains a separate bucket.
- Legacy retry aggregates are included once and visibly marked imprecise.

## Security, deployment, and coverage

The engine imports only the provider-usage and spend-history ports. It contains
no vendor endpoint/key, write, notification, threshold, or correction path. No
new setting, secret, migration, or deployment object is required. Focused tests
passed 33/33; broad related compatibility passed 353/353; Ruff, compilation, and the
mock Make journey passed.

## Residual risk and next action

Pre-C3 multi-attempt rows lack recoverable per-call timestamps and therefore
carry `legacy-aggregate` precision. Do not infer exact boundary placement for
those rows. Advance to TCA-C4 for persisted result state, drift threshold,
Notify alarm, DEGRADED maintenance behavior, and the three-state Cost badge.
