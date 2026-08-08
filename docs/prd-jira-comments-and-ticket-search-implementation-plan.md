# JIRA comments and ticket search — implementation plan

Date: 2026-08-08
Source: [prd-jira-comments-and-ticket-search.md](prd-jira-comments-and-ticket-search.md) (Draft v2)

## Delivery order and status

| Order | Item | PRD mapping | Dependencies | Status | Implementation boundary |
| ---: | --- | --- | --- | --- | --- |
| 1 | JCTS-S1 Structured search and escaping | B1.1–B1.5, G6, M3, M4 | none | Implemented | Closed structured filters, safe adapter-side JQL, mock parity, truthful page envelope, legacy `search_release` wrapper |
| 2 | JCTS-S2 Intake filters and queue handoff | B2.1–B2.3, B3.1–B3.2 | JCTS-S1 | Implemented | `AIQE_TICKET_SEARCH`-guarded UI/API, result attributes, N-of-M bulk confirmation, per-item intake validation, backward-compatible queue reads |
| 3 | JCTS-S3 Comment outcome accounting | A4.1–A4.2, M1 | none; follows S2 by PRD order | Implemented | Unconditional receipts for all five existing comment sites, run/event/plan-state homes, failure visibility without changing best-effort behavior |
| 4 | JCTS-S4 Rich plan and delivery comments | A1.1–A1.3, A2.1–A2.5, M5 | JCTS-S3 | Implemented | Flagged scenario-first plan rendering and one shared delivery/PR projection, bounded plain-text output, fused-ticket delivery |
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

| Criterion | Implementation | Verification |
| --- | --- | --- |
| B2.1 | When `AIQE_TICKET_SEARCH=1`, `GET /api/items` accepts only release/type/component/label/status/text once each, maps them to the closed S1 contract, and returns JIRA `returned`/`total` separately from `prs_returned`; the default-off path retains the legacy list response | HTTP tests cover all six filters together, unknown/repeated/raw-JQL rejection, truthful counts, flag-on envelope, and flag-off rendering/record shape |
| B2.2 | Flagged dashboard controls render issue type, components, labels, status, text, and fix version; UI distinguishes a failed request from a valid empty page and displays N of M | Rendered-HTML assertions, JavaScript syntax check, and failure-copy pins |
| B2.3 | Bulk action confirms `Queue N of M matched?` and submits each returned ticket through the existing `/api/queue` endpoint; partial failure states that earlier items remain queued | Source/functional pins confirm per-item calls, returned-page scope, exact confirmation, and partial-failure behavior |
| B3.1 | Queue entries optionally store bounded `issue_type`, `components`, `labels`, and `fix_version` provenance captured at fetch; malformed/unbounded metadata is rejected before dedupe | Queue unit and live HTTP tests verify normalized persistence, validation, duplicate validation, and legacy reads |
| B3.2 | Queue provenance is display-only; `run_all` still launches JIRA work with only source and key so runtime `get_item` remains authoritative | Runner-argument regression test proves no captured attributes cross the execution boundary |

Implementation evidence: both server and generated dashboard use UI schema 3,
while `AIQE_TICKET_SEARCH` defaults off in environment, properties, and settings
examples. The flagged endpoint validates query shape and adapter envelopes before
returning separate JIRA and PR counts. Bulk queueing is sequential through the
single-item endpoint, and queue metadata is bounded, optional, and never passed
to execution. Focused suites passed 100/100 and 36/36 after review hardening;
the broad changed-surface suite passed 325/325. Ruff checks for the new test and
modified-file syntax/undefined-name rules, Python compilation, JavaScript syntax,
and diff checks passed.

### JCTS-S3 — Comment outcome accounting

| Criterion | Implementation | Verification |
| --- | --- | --- |
| A4.1 | `ticket_comment.receipt` defines the closed payload-free attempt model; both Tracker adapters return comment ids; all pipeline ticket-comment sites call the shared best-effort boundary | Unit tests cover posted/failed/id/corrupt shapes and source pins cover routing, requirements, clarification, plan, delivery, and budget-abort calls; Jira adapter stub and adapter conformance pass |
| A4.1 run record | Per-run JSONL scratch is cleared at run start, locked on append, filtered by `run_id`, and folded into an explicit run-record `comments` block; refusal recording now happens after its delivery attempt | Full mock JIRA pipeline records one posted delivery with mock id; refusal ordering regression pin; malformed lines are counted |
| A4.1a | Plan and requirements modes pass their state key to the same helper, which retains the bounded normalized receipt in `plan_state` and creates no run record | Mock plan-from-PR journey records plan provenance and proves the run-record set is unchanged |
| A4.1 events | Every attempt emits `ticket.comment` with target/run/outcome and bounded receipt metadata; bodies and raw adapter responses are excluded | Success/failure event tests plus event vocabulary suite |
| A4.2 | Adapter exceptions, timeouts, nonzero exits, receipt-store failures, and plan-provenance failures cannot escape the helper; only sanitized exit/HTTP metadata is retained | Forced 401 fixture proves the run-facing function returns `failed`, excludes body/token text, and remains nonfatal |
| A4.2 visibility / M1 | Live and historical Run progress expose failures and corrupt counts; dashboard renders them; `make explain` gives the same requester-notification evidence | Progress/explain/UI tests cover failed delivery, HTTP detail, incomplete history, and malformed legacy counts |

Implementation evidence: the unconditional accounting boundary preserves the
Tracker port and existing best-effort behavior. Focused accounting/progress/
explain tests passed 42/42; the adjacent compatibility set passed 257/257; the
broad practical set passed 441/441. Mock plan-only and full JIRA pipeline
journeys passed, adapter conformance passed after selecting Git Bash explicitly,
and Ruff, Python/Bash syntax, rendered JavaScript, and diff checks passed.

### JCTS-S4 — Rich plan and delivery comments

| Criterion | Implementation | Verification |
| --- | --- | --- |
| A1.1 | `spec_store.render_comment` consumes the canonical structured spec; arbiter-only additions receive deterministic `adversary_added` provenance during the existing fold | Unit and mock plan journey prove scenario id/title/layer/repo plus adversary marker come from the stored spec |
| A1.2 | Scenario lines are admitted whole under `comments.max_chars` (default 8,000, capped at Jira's 32,767); omitted counts and full-plan/approval actions remain explicit | Normal, truncating, long-key, invalid-config, and hard-ceiling tests |
| A1.3 | The flagged facade returns the byte-identical legacy summary when no structured spec exists or the flag is off | Free-form and flag-off regression tests plus adjacent legacy journeys |
| A2.1 / M5 | `pr_comment.delivery_projection` is the single normalized composition consumed by `render_pr` and `render_ticket` | Behavioral identity test feeds one projection to both renderers; existing live-vs-record PR parity remains green |
| A2.2 | Ticket rendering names files/actions/scenario ids, aggregate validation, commit/no-change/quarantine/clone/refusal truth, branch/sha, reviewer and critic evidence | Unit fixtures cover every outcome; mock fused PR covers a committed plus no-change fan-out |
| A2.3 | Projection groups reported, estimated, simulated and unavailable rows separately; early budget refusal reads the same live ledger and never blends bases | Mixed-basis and simulated-refusal adversarial tests |
| A2.4 | Rich fused PR success, reviewer refusal, and budget refusal comment only the validated selected ticket and name the source PR before run-record assembly | Source ordering pin and full mock fused-PR journey with receipt in the run record |
| A2.5 | Ticket formatting is bounded plain text; control characters are sanitized and renderer failures visibly fall back before Tracker delivery | Malformed/control-character, degradation, bound, and adapter-conformance tests |

Implementation evidence: the focused rich-comment suite passed 12/12, the
closest post-review compatibility set passed 75/75, the expanded adjacent set
passed 139/139, and the bounded broad compatibility set passed 312/312.
Tracker adapter conformance, Ruff, Python/Bash syntax, and diff checks passed.
The all-registry pytest command was attempted but reached its 20-minute cap
without a result and is not counted as passing.

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
