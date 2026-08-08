# JIRA comments and ticket search — implementation plan

Date: 2026-08-08
Source: [prd-jira-comments-and-ticket-search.md](prd-jira-comments-and-ticket-search.md) (Draft v2)

## Delivery order and status

| Order | Item | PRD mapping | Dependencies | Status | Implementation boundary |
| ---: | --- | --- | --- | --- | --- |
| 1 | JCTS-S1 Structured search and escaping | B1.1–B1.5, G6, M3, M4 | none | Implemented | Closed structured filters, safe adapter-side JQL, mock parity, truthful page envelope, legacy `search_release` wrapper |
| 2 | JCTS-S2 Intake filters and queue handoff | B2.1–B2.3, B3.1–B3.2 | JCTS-S1 | Pending | `AIQE_TICKET_SEARCH`-guarded UI/API, result attributes, N-of-M bulk confirmation, per-item intake validation, backward-compatible queue reads |
| 3 | JCTS-S3 Comment outcome accounting | A4.1–A4.2, M1 | none; follows S2 by PRD order | Pending | Unconditional receipts for all five existing comment sites, run/event/plan-state homes, failure visibility without changing best-effort behavior |
| 4 | JCTS-S4 Rich plan and delivery comments | A1.1–A1.3, A2.1–A2.5, M5 | JCTS-S3 | Pending | Flagged scenario-first plan rendering and one shared delivery/PR projection, bounded plain-text output, fused-ticket delivery |
| 5 | JCTS-S5 Comment idempotency | A3.1–A3.5, M2 | JCTS-S3, JCTS-S4 | Pending | Stable visible markers, owned-comment update guard, persisted ids, unchanged skip, append-with-supersession fallback |
| 6 | JCTS-FINAL Broad verification | M1–M6, risks and constraints | JCTS-S1–S5 | Pending | Full compatibility, mock journeys, feature-flag defaults, docs/status reconciliation |

This order is the PRD's delivery plan. S1 is first because the current release
search has a live injection defect. S3 stays before rich comments so delivery
outcomes become observable before comment volume and content expand.

## Acceptance mapping

### JCTS-S1 — Structured search and escaping

| Criterion | Implementation | Verification |
| --- | --- | --- |
| B1.1 | Add Tracker `search <filters-json>`; Jira composes JQL inside its adapter and mock filters synthetic ticket fixtures in-process | Both real-adapter stub and mock-adapter functional tests; adapter conformance |
| B1.2 | Keep `search_release <release>` list output and implement it through the shared structured search path | Compatibility test plus source invariant on both adapters and existing dashboard test |
| B1.3 / G6 / M3 | Accept only closed filter names and string values; quote backslashes, quotes and control characters as JQL literals; never accept raw JQL | Exact malicious release fixture `x" OR key in (SEC-1)//` against both adapters; unknown/type adversarial tests |
| B1.4 | Define processed discovery names once in `ticket_fields`; search imports that vocabulary and adds only `status`/`text` | Set-relation regression test |
| B1.5 | Return `{items, returned, total}` for `search`; normalize all required ticket attributes; retain Jira's population total even when the page is shorter | Jira projection and mock page truncation tests |
| M4 | Exercise all six filters independently and as one ANDed combination against deterministic mock data | Parameterized adapter test |

Implementation evidence: `ticket_search.py` owns the closed structured
contract, imports the four runtime-processed names from `ticket_fields`, and
adds only discovery-only `status` and `text`. Jira builds JQL inside the
adapter from fixed fields/operators and escaped literals; mock applies the same
normalized filters to synthetic fixtures. New `search` returns all required
attributes with separate page/population counts, while `search_release` still
returns its legacy list through the shared path. The named injection value was
verified against both adapters. Targeted tests passed 42/42; the changed-surface
compatibility set passed 265/265; Bash syntax, adapter conformance, Ruff, and
Python compilation passed. The all-registry command was attempted but timed out
after 904 seconds without a result and is not counted as passing.

### JCTS-S2 — Intake filters and queue handoff

- Change `GET /api/items` to accept only the six S1 filter names and return the
  JIRA page envelope separately from known PR results.
- Add release/type/component/label/status/text controls and distinguish failed
  search from an empty page. Render `returned` of `total`.
- Bulk queue only the returned page after an N-of-M confirmation. Reuse the
  existing single-item queue endpoint for every item so validation and rate
  limits cannot be bypassed.
- Add `issue_type`, `components`, `labels`, and `fix_version` as display-only
  queue provenance. Do not pass them to pipeline execution; `get_item` remains
  the runtime authority. Treat absent legacy fields as empty.

### JCTS-S3 — Comment outcome accounting

- Introduce a pure comment-attempt result model and route all five current
  ticket comment sites through it without making comments fatal.
- Persist run-mode attempts in run-record `comments`; emit `ticket.comment`
  events for all modes; store plan-mode provenance in `plan_state` because plan
  mode intentionally creates no run record.
- Surface failed attempts in run progress and `make explain` with bounded,
  credential-free failure details.

### JCTS-S4 — Rich plan and delivery comments

- Render structured plan scenarios through `spec_store`; retain legacy summary
  for free-form plans. Apply `comments.max_chars` default 8,000 using a
  scenario-first truncation algorithm with an honest omitted-count footer.
- Extract one delivery projection from `pr_comment` and consume it from PR and
  ticket comments. Include per-repo action, files, validation, review, commit,
  branch, quarantine/refusal, and basis-labeled cost state.
- Comment the discovered ticket in fused PR runs, including refused runs, and
  guarantee a plain-text path before any optional Jira-format capability.

### JCTS-S5 — Comment idempotency

- Add visible `(aiqe:<kind>:<key>:<run>)` attribution markers and a Tracker
  `update_comment` capability.
- Persist plan ids in `plan_state` and delivery ids in comment receipts. On
  retry, update only a comment verified as authored by the configured platform
  account; otherwise append a superseding comment and record why.
- Compare normalized bodies before delivery and record `skipped_unchanged`.
  Clarification and progress comments remain append-only.

## Review and delivery gate

Each loop iteration selects exactly one row whose dependencies are complete,
reconciles the current remote PRD, refines its acceptance mapping, implements
the smallest architecture-consistent change, adds focused/adversarial tests,
runs targeted then broad practical checks, performs per-file and cross-file
correctness/security/reliability/deployment/coverage review, stages only the
item files, passes `git diff --cached --check`, commits with the JCTS item id,
pushes, and verifies local/upstream/remote parity before advancing.
