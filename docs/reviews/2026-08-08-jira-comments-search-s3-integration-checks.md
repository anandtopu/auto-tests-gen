# JCTS-S3 cross-file integration checks

Date: 2026-08-08

## End-to-end contract

`pipeline.sh` supplies a fixed kind, validated ticket key, and existing comment
body to `ticket_comment.py`. The helper selects the same mock/real Tracker
adapter as the pipeline, receives an id or failure, builds one payload-free
receipt, appends it under a lock, emits `ticket.comment`, and optionally stores
plan provenance. `run_record.py` consumes only matching-run receipts. Run
progress, dashboard, and explain read the durable projection; none influence
the run verdict or gate.

## Security and trust boundaries

- Jira traffic remains behind the Tracker port. No vendor import or direct HTTP
  client entered engine/UI code.
- Comment bodies, Jira responses, bearer/token text, and raw stderr are absent
  from receipts and events. Failure details are limited to exit code, detected
  HTTP status, timeout, or exception class.
- Target/kind/outcome/id types and lengths are validated; dashboard rendering
  escapes all receipt fields.
- Ticket replies remain data only; outcomes cannot transition tickets, plans,
  human review state, or gate decisions.

## Reliability, lifecycle, and deployment

- The boundary catches adapter, timeout, and persistence failures, and the Bash
  caller retains `|| true`; comments therefore remain best-effort.
- Per-run scratch is cleared and filtered by run id. Torn rows preserve valid
  evidence and increment an explicit incomplete-history count.
- Reviewer-refusal delivery is attempted before record assembly, so its receipt
  cannot miss the record. Normal delivery already had that ordering.
- Plan/requirements provenance uses the existing locked plan store and creates
  no run record. Old plan entries and old run records without `comments` remain
  readable.
- Both bundled adapters now return comment ids; the real Jira shape is exercised
  with a stubbed Jira response and the mock shape in full pipeline journeys.

## Coverage evidence

- Focused accounting/progress/explain: 42 passed.
- Adjacent event/UI/plan/reviewer/adapter suites: 257 passed.
- Broad practical compatibility: 441 passed.
- Mock plan-only journey: posted plan receipt in `plan_state`, no new run record.
- Full mock JIRA journey: posted delivery receipt and id in the run record.
- Adapter conformance: passed. The first invocation selected the unavailable WSL
  shim; the Git-Bash-path retry passed and is the counted result.
- New-test full Ruff, changed-file correctness Ruff, Python compilation, Bash
  syntax, rendered JavaScript parsing, and diff checks passed.

## Residual risk

Receipt persistence and the event log are both best-effort because observability
must not abort paid work. If both stores are unavailable, stderr reports degraded
accounting but the run continues. This is intentional A4.2 behavior, not a claim
of guaranteed delivery telemetry under total storage failure.
