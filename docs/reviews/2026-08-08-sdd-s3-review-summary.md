# Review summary: SDD-S3 adoption levels

Date: 2026-08-08
Branch: `codex/test-knowledge-a1-a2`

## Outcome

SDD-S3 is implemented and ready to push. Four adoption levels now present one
decision over the existing controls, while effective truth still comes from the
engine resolvers. Enforced coverage visibly distinguishes warn dry-run from
strict enforcement, and unmatched or unusable configurations remain Custom.

## Two-pass findings

| ID | Severity | Status | Finding | Resolution |
| --- | --- | --- | --- | --- |
| S3-01 | P1 | Fixed | `env_flag` suppresses repeat warnings, so an invalid spec-mode value could lose its Custom/problem signal after the first refresh | Resolve through `env_flag`, but independently retain its accepted-vocabulary problem on every governance read |
| S3-02 | P2 | Fixed | Advanced raw governance edits saved correctly but the running dashboard kept the old resolved level | Refresh managed env values only when mapped controls change, then reload all governance surfaces |
| S3-03 | P2 | Fixed | A non-string enforcement sub-state needed a typed error and an explicit HTTP 400 boundary | Raise `TypeError`, catch it at the route, and add numeric adversarial coverage |
| S3-04 | P2 | Fixed | Timeout-aborted broad verification left test-mutated PROJ-301 artifacts and a live lock/process tree | Stop only confirmed child processes, remove the verified stale lock, restore both tracked fixtures exactly, and rerun the focused set |
| S3-05 | P3 | Residual | Q1/Q4 lack a named product decision | Retain existing Reviewed-plans default behavior and internal constitution terms; document both as reversible assumptions |

## Validation evidence

| Check | Result |
| --- | --- |
| Focused settings/usability | 73 passed |
| Authenticated adversarial API | 115 passed initially; valid apply/restore and numeric malformed case added afterward |
| Combined SDD/resolver/event/settings/API set | 258 passed |
| Post-review SDD + authenticated API | 165 passed |
| Exact-control mutation | Expected failure after adding a fourth control; 1 passed after restore |
| Compile and new-module Ruff | Pass |
| Isolated dashboard + governance JSON | Pass; current level resolved to Reviewed plans |
| `make review` | Incomplete: runner timeout after 904 seconds; no pass claimed |
| Complete `registry/tests` | Incomplete: runner timeout after 604 seconds; no pass claimed |

The timeout-aborted runs were audited for lingering processes and tracked-file
pollution. Confirmed children were stopped, the stale lock removed, and the
PROJ-301 tracked fixtures restored exactly before the final 258/258 and 165/165
evidence was accepted.

## Next eligible item

SDD-S4 — conditional acceptance-criteria wizard step and signed/prose-aware
approval benefit confirmation.
