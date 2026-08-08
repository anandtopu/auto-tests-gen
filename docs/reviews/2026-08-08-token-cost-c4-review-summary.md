# TCA-C4 review summary

## Outcome

Ready to commit. Reconciliation is now an operationally honest, read-only loop:
persist, classify, notify on material drift, display one of three states, and
run nightly without confusing an external outage with a successful check.

## Correctness and reliability

- The default 10% threshold uses exact Decimal evidence and strict “beyond”
  semantics. Undefined percentage on a non-zero mismatch is always drift.
- Missing credentials, provider unavailability, timeout, absent state, and
  corrupt state all remain `not-reconciled`.
- Notification failure is visible in state and maintenance; it cannot erase the
  completed drift evidence.
- Exit 75 is the narrow external degradation contract. Local configuration and
  persistence faults retain exit 1 and fail the nightly job.

## Security, deployment, and coverage

Provider access still crosses only the adapter port; no vendor endpoint or key
entered the engine. Notify delivery uses the established port and audit trail.
The state file is locked, atomic, relocatable, bundled, resettable, ignored, and
contains no credential. Focused tests passed 32/32; functional mock and
missing-credential journeys matched their expected states and exits; the broad
compatibility set passed 267/267.

## Residual risk and next action

A persistent drift can alert on consecutive nightly runs. That preserves the
PRD’s alarm guarantee and the message supplies both investigation causes; add a
policy-level cooldown only if operators establish a separate acknowledgment
contract. Advance to TCA-FINAL for the full compatibility sweep and final PRD
status reconciliation.
