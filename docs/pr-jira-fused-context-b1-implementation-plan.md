# B1 Implementation Plan — Read-only Test Reviewer

Date: 2026-08-06
Status: Implemented, validated, and reviewed
PRD: docs/prd-pr-jira-fused-context-multi-agent.md §5 B1
Branch: codex/test-knowledge-a1-a2

## Acceptance mapping

| Requirement | Implementation | Verification |
| --- | --- | --- |
| B1 placement and evidence | REVIEW_TESTS runs after validate/relocation and before gate. It compares source with plan-or-triage, fused ticket data when present, and the target repo conventions/catalog slice. | Pipeline-order pins and fully mocked PR run |
| B1.1 read-only | Org config grants the reviewer phase exactly Read; its prompt also forbids writes, tests, and git. | Phase inventory, tool-policy, and prompt tests |
| B1.2 semantic scope | Closed categories allow only missing coverage, vacuous assertions, ticket mismatch, and non-lint convention violations. | Closed-enum malformed-output tests |
| B1.3 unavailable is nonfatal | Phase, input, or strict-contract failure records per-repo unavailable; the merged signal never contributes to gate-derived overall. | Failure aggregation and run-record tests |
| B1.4 zero-test skip | Each zero-test repo uses SKIP_PHASE reviewer-&lt;repo&gt; and merges as skipped, distinct from approval and outage. | Zero-test unit test and two-repo mock run |
| B1.5 fan-out mirror | Every resolved test repo gets isolated source, conventions, catalog, label, verdict, and status. Unstamped multi-repo tests are rejected rather than guessed. | Isolation, confinement, mixed-verdict, and fan-out tests |
| B1.6 scripted mock | The mock supports approve, needs_work, and malformed scripts and stamps simulated true; those results prove plumbing only. | Mock pin and mocked needs_work run |

## Implementation boundary

- engine/lib/test_reviewer.py confines generated paths below the resolved
  workspace/tests/&lt;repo&gt; checkout. Missing, oversized, absolute, or traversing
  paths make that repo review unavailable.
- Reviewer calls run outside the budget-guarded PHASE wrapper so an advisory
  outage after paid generation and validation cannot discard the gate result.
  Reviewer spend and phase inputs are still recorded.
- Per-repo and merged contracts persist in the run record. B1 excludes repairs,
  delivery enforcement, human-facing verdict surfaces, and reviewer-quality
  claims; those remain B2, B3, B4, and B6.
- AIQE_TEST_REVIEWER and review.enabled remain off by default. The configured
  Haiku tier is provisional until B6 provides real-model evidence.

## Validation evidence

- Focused B1, inventory, and artifact-boundary suite: 44 passing tests.
- Compatibility checks for routing, cache, settings, spend, run records, and
  the existing critic: 178 passing tests.
- Fully mocked PR #201: API returned scripted needs_work, the zero-test UI repo
  recorded skipped, the run record labelled the result simulated, and gates
  still produced committed/no-changes.
- Full registry suite: 1,489 passing tests across 118 files, executed in four
  isolated shards after the monolithic command reached its 10-minute ceiling.
- Two-pass review fixed three P2 hardening/integrity findings and two P3
  mock/configuration findings. No P0, P1, or P2 finding remains open.

## Residual work and rollout

B4 is next and owns the review board, comments, progress, and explain surfaces.
B6 then measures seeded and clean fixtures, including real-model quality when
parity is available. Until those items land, the durable run record is the only
supported reviewer surface and mock verdicts are plumbing data.
