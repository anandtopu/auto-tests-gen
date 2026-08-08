# TCA-C4 action register

| ID | Priority | Finding | Resolution | Status |
|---|---:|---|---|---|
| C4-01 | P1 | A provider subprocess timeout escaped the normal unavailable payload, so maintenance failed locally and the dashboard retained stale status instead of `not reconciled`. | Convert port timeout into persisted unavailable evidence and external exit 75; regression added. | Fixed |
| C4-02 | P1 | A boolean maintenance tolerance would hide local persistence/configuration bugs as external outages. | Add exit-code-specific degradation policy: only 75 degrades; exit 1 fails. | Fixed |
| C4-03 | P1 | Provider-zero/platform-nonzero drift has no finite percentage and could be mislabeled below threshold. | Treat every non-zero undefined-percentage mismatch as drift; adversarial test added. | Fixed |
| C4-04 | P2 | The generic degraded summary said a step “did NOT run,” although reconciliation may run and return unavailable. | Report that it did not complete successfully, preserving the non-green signal without a false claim. | Fixed |
| C4-05 | P2 | HTML-escaping text before assignment to the DOM `title` property produced encoded tooltip text. | Assign a string through the safe property; no HTML sink is used. | Fixed |
| C4-06 | P2 | Reconciliation alarms may repeat on every nightly window while drift persists. | Retain one alarm per completed run as required; existing Notify retries/audit apply. Cooldown is not added because C2.3 requires notification for each breach. | Accepted |

Next item: TCA-FINAL broad verification.
