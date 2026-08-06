# A2 Implementation Plan — PR + JIRA Context Fusion

Date: 2026-08-06
Status: Reviewed; ready for implementation
PRD: [prd-pr-jira-fused-context-multi-agent.md](prd-pr-jira-fused-context-multi-agent.md) §5 A2
Baseline: `c9a4a3f` on `codex/test-knowledge-a1-a2`

## Outcome

When A1 selects a validated ticket, PR triage and per-repository generation will
receive the ticket requirements and the same story/bug/security guidance as the
JIRA path. Ticket content remains untrusted data, acceptance criteria cannot be
trimmed, description/comment prose remains budgeted, and the context block stays
in the run-specific prompt tail. Flag-off and no-selection executions retain the
A1 baseline.

## Scope

In scope:

- harden A1 response identity before any ticket is eligible for fusion;
- materialize the selected response at canonical `out/ticket.json` without a
  second Tracker request;
- reuse and extend the single `ticket_fields.py` parse for guidance selection;
- render a deterministic, framed, budget-aware ticket block;
- provide the block and issue guidance to PR triage and generation only;
- persist enough manifest evidence to explain what was included or omitted;
- add focused unit, pipeline, security, compatibility, and adversarial tests.

Out of scope:

- PR plan-first lifecycle (A3);
- discovery metrics/fixtures (A4), except fixtures needed to validate A2;
- reviewer phases (Epic B);
- Confluence fusion or new Tracker calls;
- changing routing from PR mode to JIRA mode.

## Review findings that shape the design

| ID | Severity | Observed gap | Required disposition |
| --- | --- | --- | --- |
| A2-F1 | P1 | `pipeline.sh` marks a candidate valid on Tracker exit 0 but does not confirm that returned JSON has the requested `key`. | Validate parseability, object shape, and exact key identity before writing a `valid` TSV row. Mismatch/unparseable output is `validation_unavailable` and cannot be fused. |
| A2-F2 | P1 | A1 stores `out/discovered-ticket.json`; the established JIRA path and scoping machinery consume `out/ticket.json`. | Atomically promote only the selected, identity-checked response to `out/ticket.json`; do not fetch again or add a parallel schema. |
| A2-F3 | P1 | `context_scope.py` reads a ticket only as a retrieval signal; it does not render ticket requirements or reserve acceptance criteria. | Add a ticket-tail renderer whose mandatory block survives any budget and whose optional prose is bounded by the existing phase budget. |
| A2-F4 | P2 | Issue-guidance selection is embedded in the non-PR branch of `pipeline.sh`. | Extend `ticket_fields.py` to emit one normalized guidance kind and have both paths copy the same existing prompt. |
| A2-F5 | P2 | Passing raw ticket JSON would bypass explicit omission accounting and weaken prompt-tail/cache-order guarantees. | Render a framed Markdown tail with a manifest; never pass raw ticket JSON to PR phases. |
| A2-F6 | P2 | “Today” in A2.5 is ambiguous after A1 introduced explicit no-ticket state. | Define parity against commit `c9a4a3f`: flag-off has no discovery/fusion activity; flag-on with no selection keeps A1's discovery-state tail but adds no ticket/guidance files. |

## Target data flow

```text
PR intake
  -> A1 SCM signals
  -> Tracker get_item(candidate)
  -> response identity validation
  -> A1 deterministic selection
       no selection -> A1 discovery-state tail only
       selected     -> atomic out/ticket.json
                         -> ticket_fields.py (one parse)
                         -> shared issue-guidance.md
                         -> budgeted framed ticket tail
                         -> PR triage and generate context tails
```

The PR remains a PR run: diff-driven routing, PR run key, PR comments, gate
behavior, and generated-test destination are unchanged.

## Design decisions

### D1 — Canonical materialization, no refetch

Add a small pure validation operation to `ticket_discovery.py` (or a narrowly
scoped sibling if implementation proves cleaner): given candidate `K` and its
response file, succeed only when the file is valid JSON, is an object, and its
top-level `key` equals `K`. The pipeline records `valid` only after this check.
After selection, copy through a temporary file and rename to `out/ticket.json`.
`out/discovered-ticket.json` may remain as an A1 compatibility/provenance alias,
but all A2 consumers use `out/ticket.json`.

Acceptance checks:

- success with the wrong key, an array, malformed JSON, or an empty file never
  creates `out/ticket.json` and never reaches a prompt;
- the selected candidate causes exactly one Tracker request;
- the canonical bytes equal the identity-checked response bytes.

### D2 — One parse and one guidance policy

Extend `ticket_fields.fields()` with `AIQE_T_GUIDANCE=story|bug|security`, using
the current precedence exactly: security label overrides; otherwise security
issue type; otherwise bug/defect; otherwise story. Both JIRA and selected-ticket
PR paths call `ticket_fields.py out/ticket.json` once and copy
`prompts/issue-types/${AIQE_T_GUIDANCE}.md` to `out/issue-guidance.md`.

This replaces the inline shell selection rather than duplicating it. Existing
JIRA outputs must remain byte-compatible apart from the new exported variable.

### D3 — Framed, deterministic ticket tail

Add `engine/lib/ticket_context.py` with a pure renderer returning Markdown plus
a manifest. The renderer accepts only a parsed ticket object and emits:

1. a fixed warning that ticket text is data, never instructions;
2. selected key, discovery provenance summary, issue type, components/labels;
3. summary and every acceptance criterion as mandatory content;
4. description and latest comments as optional content in deterministic order;
5. explicit `included`/`omitted` fields, character counts, and omission reasons.

All strings are rendered as inert text. The helper performs no port, shell,
workspace, or network operation. It uses bounded field/list sizes even for an
unscoped phase, so a hostile ticket cannot create an unbounded prompt.

### D4 — Budget integration without breaking cache order

Keep the estate/scoped context file first. Create a separate
`out/pr-ticket-context-<phase>.md` tail after estate assembly and pass it after
the diff/contract and other run-specific inputs.

For a phase with context scoping enabled:

- read the existing `context_scope` manifest (`budget_tokens`, `used_chars`);
- acceptance criteria remain mandatory even when they exceed the remaining
  budget, matching the existing MUST-KEEP overflow rule;
- description/comments consume only the remaining optional budget and record
  every omission;
- do not duplicate ticket text inside `out/context-<phase>.md`.

For an unscoped phase, apply the renderer's safety caps but not the scoped phase
budget. `generate` is currently unscoped, so its framed ticket file is appended
at the final run-specific tail. This preserves the prompt assembly rule in
`run_phase.sh`: stable prompt and estate context first, per-run ticket last.

### D5 — Closed outcome behavior

| Flag | Discovery outcome | PR phase additions |
| --- | --- | --- |
| off | not looked | none; A1 baseline byte parity |
| on | selected | `out/ticket.json`, shared guidance, framed ticket tails |
| on | not_found | A1 `No ticket discovered.` tail only |
| on | ambiguous | A1 candidate/refusal tail and comment only |
| on | discovered_invalid | A1 rejected-key tail only |
| on | validation_unavailable | A1 unavailable tail only |

No non-selected outcome may leave a stale `out/ticket.json` or guidance file.
Scratch setup must remove or overwrite these artifacts before selection.

## Work packages

### WP1 — Harden and materialize

Files:

- `engine/lib/ticket_discovery.py`
- `engine/pipeline.sh`
- `registry/tests/test_ticket_discovery.py`

Tasks:

1. Add exact response-identity validation and CLI coverage.
2. Classify malformed/mismatched success as unavailable with a bounded reason.
3. Promote the selected response atomically to `out/ticket.json`.
4. Prove one Tracker call per candidate and no selected artifact on refusal.

### WP2 — Share field and guidance policy

Files:

- `engine/lib/ticket_fields.py`
- `engine/pipeline.sh`
- `registry/tests/test_ticket_fields.py`
- `registry/tests/test_p0_features.py`

Tasks:

1. Add pure guidance classification to the single parse.
2. Replace inline JIRA guidance branching with the shared output.
3. Invoke the same operation after PR ticket materialization.
4. Pin story, bug, defect, security-type, and security-label precedence.

### WP3 — Build budget-aware fusion artifact

Files:

- new `engine/lib/ticket_context.py`
- `engine/lib/context_scope.py`
- `engine/lib/run_record.py`
- new `registry/tests/test_ticket_context.py`
- `registry/tests/test_context_scope.py`

Tasks:

1. Implement deterministic framing, bounds, and manifest output.
2. Use scoped manifest evidence to calculate optional remaining space.
3. Keep all acceptance criteria under a one-token/over-budget test.
4. Drop/truncate description/comments deterministically and name omissions.
5. Snapshot phase inclusion/omission evidence into the run record.

### WP4 — Wire PR phases at the correct tail

Files:

- `engine/pipeline.sh`
- new `registry/tests/test_ticket_fusion.py`
- optionally `engine/lib/phase_cache.py` tests if its input contract changes

Tasks:

1. Pass guidance and the phase-specific ticket tail to triage and every
   generation fan-out invocation only after a ticket is selected.
2. Keep the context arrays empty for all other outcomes.
3. Assert argument order: estate context before diff/contract, ticket block at
   the run-specific tail.
4. Assert per-repo fan-out receives identical ticket requirements and the
   target repo's existing isolated conventions.
5. Confirm phase-cache keys include ticket/guidance bytes when fused and are
   unchanged when disabled/no selection.

### WP5 — Documentation and rollout evidence

Files:

- `docs/architecture.md`
- `.env.example`
- `aiqe.properties.example`
- `docs/prd-pr-jira-fused-context-implementation-plan.md`
- A2 multi-pass review reports

Tasks:

1. Document selected/no-selection flow, framing, budgeting, and rollback.
2. Keep `AIQE_PR_TICKET_CONTEXT=0` as the only slice flag.
3. Mark A2 complete only after focused and broad checks pass and the pushed
   commit is verified on origin.

## Acceptance mapping

| PRD criterion | Implementation evidence | Required tests |
| --- | --- | --- |
| A2.1 canonical/single parse | selected response becomes `out/ticket.json`; both paths use `ticket_fields.py` once | no-refetch, identity mismatch, one-parse/pipeline tests |
| A2.2 issue guidance | one shared guidance classifier and existing prompt files | story/bug/defect/security type/security label; PR/JIRA parity |
| A2.3 cache order | framed file is a separate final context argument | phase-input ordering and phase-cache-key tests |
| A2.4 scoped budget | renderer uses scoped manifest; AC mandatory, prose optional and audited | 1-token AC survival, optional omission, deterministic bytes, manifest completeness |
| A2.5 parity | arrays/files exist only for selected outcome; baseline is `c9a4a3f` | flag-off golden trace, every non-selected outcome, stale-artifact adversarial case |
| Data-never-instructions | fixed frame, pure renderer, no tool authority | instruction-shaped ticket fixture; output remains data and cannot alter paths/commands |

## Test matrix

Minimum focused scenarios:

1. Explicit valid bug ticket: selected, canonicalized, bug guidance, fused twice
   (triage and generate), one Tracker call.
2. Branch valid security-labelled story: security guidance wins.
3. Wrong-key/malformed successful Tracker response: unavailable, no fusion.
4. Two valid non-branch keys: ambiguity, no ticket/guidance artifact.
5. Flag off: phase file list, adapter calls, cache key, and discovery artifacts
   match the `c9a4a3f` baseline.
6. Flag on/no key: exact A1 `No ticket discovered.` state; no fused ticket.
7. One-token scoped budget: every AC retained, description/comments omitted and
   named; same inputs produce identical bytes.
8. Hostile ticket text containing prompt instructions, shell syntax, long lists,
   and traversal strings remains inert, bounded, and framed.
9. Multi-repo fan-out: every generator gets the same ticket tail but only its
   own conventions/catalog slice.
10. Existing JIRA workflow: guidance, ticket parsing, comments, routing, and
    generate inputs remain compatible.

## Validation sequence

1. Ruff on new/changed Python and tests.
2. `bash -n engine/pipeline.sh`.
3. Focused discovery, ticket-fields, ticket-context, context-scope, P0 guidance,
   phase-cache, and fusion tests.
4. Adapter conformance (no port contract change expected).
5. Full `registry/tests` compatibility suite.
6. Two-pass review: per-file, then cross-file correctness/security/reliability,
   cache-order, deployment/flag, and coverage checks.
7. `git diff --cached --check`, exact-file commit, push, and origin parity.

## Rollback and observability

Rollback is `AIQE_PR_TICKET_CONTEXT=0`; no new persistent store or migration is
introduced. Run records should distinguish `selected-and-fused`, selected but
fusion unavailable, and every A1 no-selection state. The ticket-context manifest
must show mandatory/optional bytes and omissions so `make explain` can later
answer what requirements the agents actually saw.

## Completion definition

A2 is complete only when every acceptance row above has executable evidence,
the A1/JIRA compatibility paths are green, no unresolved P0/P1/P2 review finding
remains, and the iteration commit is present on the configured origin branch.
