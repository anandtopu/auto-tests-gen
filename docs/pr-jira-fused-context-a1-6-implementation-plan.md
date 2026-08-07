# A1.6 Implementation Plan — Terminal Ticket Provenance and Warnings

Date: 2026-08-06
Status: Implemented and validated
PRD: `docs/prd-pr-jira-fused-context-multi-agent.md` §5 A1.6
Branch: `codex/test-knowledge-a1-a2`

## Acceptance mapping

| Requirement | Implementation | Verification |
|---|---|---|
| Record selected ticket status in provenance | Extend the Tracker ticket shape with bounded `status` and `status_category`; annotate the selected discovery artifact from the already-fetched canonical ticket. Missing status remains explicit `unavailable`, never an empty success. | adapter-shape, annotation, run-record tests |
| Detect terminal status consistently | One pure `ticket_discovery` classifier treats normalized `Closed`/`Done` names or JIRA's `done` status category as terminal. | case, whitespace, custom-name/category, malformed tests |
| Warn without refusing | Store a bounded warning in provenance while preserving `outcome: selected`; generation and gate flow remain unchanged. | selected-terminal unit and functional pipeline tests |
| Warn on every selected-ticket surface | Render the same provenance through discovery context, fused authoring context, `make explain`, and live/historical PR coverage comments. | cross-surface exact-warning assertions |
| Preserve default-off/no-ticket behavior | Annotation occurs only after a validated selection under `AIQE_PR_TICKET_CONTEXT`; no new files or calls occur otherwise. | source-order and flag/no-selection compatibility tests |

## Implementation sequence

1. Add status fields to the JIRA adapter and mock benchmark ticket.
2. Validate status bounds and add a pure selected-ticket annotation operation.
3. Atomically annotate `out/ticket-discovery.json` after canonical promotion.
4. Consume the recorded status/warning on authoring, explain, and PR-comment surfaces.
5. Run focused tests, broad compatibility checks, and two-pass review.

## Safety and rollback

Ticket status is untrusted data: it is bounded, Markdown-safe, and never changes
tools, routing, selection, or gate decisions. A terminal state warns but does
not refuse. Rollback remains `AIQE_PR_TICKET_CONTEXT=0`; no migration or new
persistent store is introduced.

## Implementation evidence

- The JIRA adapter now returns bounded status name and status-category fields;
  the mock benchmark ticket pins the active-state path.
- The selected discovery artifact records normalized status evidence and a
  terminal warning without changing its `selected` outcome.
- Discovery context, fused authoring context, `make explain`, and live or
  historical PR coverage comments all render the recorded state.
- Focused validation passed 42 tests; the full registry suite passed 1,451
  tests in 743.25 seconds. Changed Python passed Ruff and both modified shell
  scripts passed Git Bash syntax validation.
- The two-pass review fixed one P2 finding: user-facing consumers recompute
  terminal state from bounded fields rather than trusting a stored boolean.
