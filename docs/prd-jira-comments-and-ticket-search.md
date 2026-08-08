# PRD — JIRA comments worth reading, and finding the work by any field

|  |  |
|---|---|
| **Status** | Draft v2 — revised after adversarial gap review (Appendix B) |
| **Author** | Product Management (QE Platform) |
| **Date** | 2026-08-06 |
| **Doc** | `docs/prd-jira-comments-and-ticket-search.md` |
| **Related** | [integrations/jira.md](integrations/jira.md) · [prd-pr-jira-fused-context-multi-agent.md](prd-pr-jira-fused-context-multi-agent.md) (fusion + reviewer, now landing) · `engine/lib/pr_comment.py` (the fidelity benchmark) |

**The ask:** update JIRA tickets with test-plan details in a comment; comment
the generated E2E tests back onto the ticket; and let the UI filter and fetch
tickets by fixVersion, issue type, component, or any supported field — with the
fetched information feeding plan and test generation.

**The honest framing:** the platform already comments tickets at five points in
a run, already fetches by fixVersion, and already *processes* every field the
ask names (components route, issue type selects guidance, fixVersions become
the release). What is missing is narrower and better than a greenfield build:
the ticket's comments are **terse where the PR's are rich**, comments are
**fire-and-forget** (reposted on every retry, failures silently swallowed), the
Tracker port searches by **exactly one field**, and — found while writing this —
the one existing search **interpolates free UI text into a quoted JQL string**.

---

## 1. What exists today (verified, with line numbers)

**Commenting — five sites, all best-effort:**

| Site | Content | Where |
|---|---|---|
| Routing needs clarification | candidates + how to reply | `pipeline.sh:677` |
| Requirements authored | draft awaiting validation | `:832` |
| Blocking ambiguity | the question, exit 65 | `:847–849` |
| Plan authored (draft) | plan path + adversary line, awaiting approval | `:933` (and `PLAN_TICKET` routing for plan-from-PR, already landing) |
| Run summary | per-repo gate status + critic line + reviewer line | `:982–984`, `:1105–1107` |

Plus `make attach-plan` / `plan-link` (Tracker `attach` verb, recorded via
`plan_state.mark_linked`) and `publish-plan` (Confluence mirror).

**The fidelity benchmark that the ticket never gets:** `pr_comment.py` builds
the PR's coverage comment — behaviors covered, tests created vs. updated,
validation outcome, per-repo gate results, critic signal, cost — and
`from_record()` can rebuild it from any run record (`GET /api/pr-coverage`).
The ticket's `SUMMARY` is a handful of status lines. Same run, two audiences,
one of them short-changed — and it is the audience that asked for the work.

**Search:** the Tracker port speaks exactly four verbs — `get_item`,
`search_release`, `attach`, `comment`. `search_release` takes one fixVersion
string; the dashboard's fetch (`GET /api/items?release=`) is its only caller.
Component/label inputs exist in the UI only on the pasted-ticket (inline) card.

**Processing:** everything the ask wants "stored and used" is already consumed
at run time — `resolve.py` routes on components/labels, issue type selects
`prompts/issue-types/*`, fixVersions auto-capture as the key's release, and
`ticket_fields.py` extracts all of it from `out/ticket.json` in one parse.

**The gaps:**

| # | Gap | Evidence |
|---|---|---|
| **G1** | Ticket comments lack the detail the ask names: no scenario-level plan comment, no PR-fidelity generated-tests comment | `SUMMARY` construction vs. `pr_comment._compose()` |
| **G2** | Comments are fire-and-forget: every retry re-posts, failures are swallowed (`\|\| true` at all five sites), and **no record says whether any comment landed** — "commented" is assumed, never established (C13) | run record has no comment field; grep confirms |
| **G3** | One search verb, one field | `adapters/tracker/*.sh` |
| **G4** | UI cannot filter by type/component/label/status/text | `dashboard_server.py:158` — release is the only query |
| **G5** | Queue items carry no ticket attributes (mode/target/pr/release only), so the queue cannot display or filter what was fetched | `work_queue.py` item shape |
| **G6** | **JQL injection shape in the existing search**: `JQL="fixVersion = \"$1\""` (`adapters/tracker/jira.sh:74`) interpolates free UI text into a quoted JQL literal — a `"` in the release box escapes the string and rewrites the query. Blast radius today is a read-only search under the user's own token; Epic B multiplies this surface by six fields, so it gets fixed *first*, not alongside | read directly off the adapter |

---

## 2. Users

| Persona | What changes |
|---|---|
| **QA / requester** | The ticket they filed shows, in its own comment thread, what will be tested (plan, per scenario) and what was delivered (tests, per file, with outcomes) — without opening the platform |
| **LEAD** | Fetch "all `Bug`s in component `checkout` for `2.14`" and queue them as a set; the queue shows what each item *is* |
| **Stakeholder with JIRA only** | The comment thread is the audit trail: proposed → approved → delivered, each entry attributable to a run |

---

## 3. Goals and non-goals

### 3.1 Goals

1. A **plan comment** at scenario fidelity when a plan is authored, and a
   **delivery comment** at PR-comment fidelity when tests are generated.
2. Comments are **idempotent and accounted for**: retries update rather than
   re-post, unchanged content is never re-sent, and every attempt's outcome is
   recorded.
3. The Tracker port **searches by structured filters** — fixVersion, issue
   type, component, label, status, free text — safely composed.
4. The UI exposes those filters, shows the attributes, and can queue a
   filtered set; queue items carry what was fetched.
5. Run-time processing keeps its source of truth: `get_item` at run start.

### 3.2 Non-goals

- **Not** two-way sync: no ticket transitions, no field edits, no status
  changes. The platform comments and attaches; the ticket's workflow is not
  ours (unchanged from today).
- **Not** a ticket cache store. Search results are pass-through; what persists
  is queue items (an existing store) now carrying attributes. Freshness beats
  caching: the run refetches `get_item` at start, and that stays **pinned** —
  a stale cached ticket driving generation is the fused-context work's
  wrong-ticket risk, self-inflicted. (This also means no sixth state store and
  no new store risks.)
- **Not** comment threads as a control channel. Ticket text — including
  replies to our comments — remains data, never instructions (constitution).
- **Not** arbitrary raw JQL from the UI. Structured filters only; power users
  who want raw JQL have JIRA. (This is a security posture, not a convenience
  judgment — see B1.3.)

---

## 4. Epic A — Comments worth reading

### A1. The plan comment

**Requirement.** WHEN a plan is authored (jira `plan` mode, and plan-from-PR
once landed), the ticket comment SHALL carry scenario-level detail: per
scenario its id, title, layer/target repo; adversary-added scenarios marked as
such; the approval ask and how to act on it.

- **A1.1** — The scenario list SHALL be rendered **through `spec_store`'s
  rendering** — the one-source-of-truth rule that already governs
  `testplans/<KEY>.md`. A second scenario renderer in the comment builder is
  how the comment and the plan drift.
- **A1.2** — Length SHALL be bounded by org-config `comments.max_chars`
  (default **8,000** — JIRA's hard ceiling is 32,767 and a comment anywhere
  near it is unreadable): scenario lines first, then truncation stated
  honestly — "N more scenarios — full plan attached/linked" — with the
  existing attach/link mechanisms unchanged. A truncated list that does not
  say so reads as the whole plan.
- **A1.3** — Free-form (legacy) plans get the current summary comment
  unchanged — no structured spec means nothing to itemize, and inventing
  structure from prose is a lie with bullet points.

### A2. The delivery comment

**Requirement.** WHEN a run generates tests for a ticket (jira mode, or tests
mode resuming an approved plan), the ticket SHALL receive a delivery comment at
the PR comment's fidelity: tests created vs. updated (per file, with its
scenario ids), validation outcome, per-repo gate result with branch name,
reviewer/critic lines as today, and the basis-labelled cost line.

- **A2.1** — **One projection, two renderings.** The comment SHALL be built
  from the same composition `pr_comment.py` uses (reshaped for JIRA's
  formatting), and a pin SHALL assert the two builders share it — two parallel
  "what did this run deliver" implementations is the two-definitions defect
  this platform keeps paying for.
- **A2.2** — The comment states what is true per repo: a `NO_CHANGES` repo
  says so; a quarantined repo says why; a refused run (reviewer `require`,
  budget abort) comments the refusal with the named fix rather than going
  silent — the requester watching the ticket must not need the platform UI to
  learn nothing arrived.
- **A2.3** — Cost renders under the iron rule: basis-labelled, `~` for
  estimated/simulated, never a blended number.
- **A2.4** — **PR-mode runs with a discovered ticket comment that ticket too
  (v2 decision — resolves the fused-context PRD's open question E5).**
  Plan-from-PR delivery already routes to the discovered ticket
  (`PLAN_TICKET`, `pipeline.sh:1105`); a plain `pr`-mode run with a fused
  ticket commented only the PR, so the requester watching the ticket the PR
  implements heard nothing. The delivery comment goes to both surfaces; the
  ticket's copy names the PR it came from. One decision, made once, cited by
  both PRDs.
- **A2.5** — **Plain-text-safe rendering is the guaranteed floor**, whatever
  the ADF decision (Q1): every comment SHALL render meaningfully as plain
  text, with richer formatting a per-adapter capability on top — never a
  precondition. A comment that requires ADF to be readable fails closed on
  Server/DC.

### A3. Idempotency — comments that update instead of accumulate

**Requirement.** Re-running a key SHALL NOT accumulate duplicate comments.

- **A3.1** — Every platform comment SHALL carry a stable, machine-readable
  marker (kind + key) as a **visible but unobtrusive footer line**
  (`⚙ aiqe:delivery:PROJ-410 · run 1754…`) — v1 said "invisible", and JIRA
  plain-text comments have no reliable hidden markup; pretending otherwise
  would have surfaced as a formatting surprise in S4. The visible footer also
  gives the requester run attribution for free.
- **A3.2** — The Tracker port gains an **`update_comment`** verb, capability
  ed like the LLM adapters' `tool_policy`: an adapter that cannot update
  (API limitation, permissions) says so, and the platform falls back to
  append-with-supersession ("supersedes the plan comment above") — stated
  fallback, never silent duplication.
- **A3.2a** — **Only comments authored by the platform's own account are ever
  updated.** A marker is not authority: a third party's comment carrying a
  forged or coincidental marker must never be edited by us — that is
  tampering with a human's words, and on most deployments a permissions
  error besides. Authorship mismatch → the append-with-supersession path,
  and the mismatch is recorded.
- **A3.5** — **How a later run finds the comment to update (v2 — v1 specified
  update semantics with no way to locate the target):** posted comment ids
  are **persisted where the comment's subject lives** — `plan_state` for plan
  comments (the `mark_linked` precedent, same store, same shape) and the run
  record's `comments` block for delivery comments, which a retry reads from
  the key's prior run records (a store and lookup that already exist). A
  `find_comment` search verb was considered and rejected: it widens the port
  for a lookup the platform's own records can answer, and a marker search
  against a long comment thread is the slow, spoofable version of reading
  our own receipt.
- **A3.3** — Unchanged content SHALL NOT be re-sent at all: same marker, same
  rendered body → skip, recorded as `skipped_unchanged`. A retry that changes
  nothing should leave no trace on the ticket.
- **A3.4** — Progress/clarification comments (the ask-a-human sites) keep
  append semantics — a question re-asked after a state change is new
  information, and editing history under a human's feet is worse than a
  duplicate.

### A4. Outcomes recorded — "commented" becomes a fact

**Requirement.** Every comment attempt SHALL record its outcome.

- **A4.1** — The run record gains a `comments` block: per attempt — kind,
  target ticket, comment id when posted (A3.5's lookup source), outcome
  (`posted | updated | skipped_unchanged | failed`), and the failure detail
  when failed. The event log gets the same (`ticket.comment` kind). Today
  `|| true` swallows everything: a tracker outage means the requester never
  heard, and nothing anywhere knows.
- **A4.1a** — **Plan-mode outcomes have a stated home, because a run record
  does not exist there.** The plan comment (A1 — the headline feature) is
  posted by plan mode, which writes no run record *by design* — v1's "the run
  record gains a comments block" was unimplementable for the feature's own
  flagship path. This is the cost PRD's G1 shape (an invariant silently
  taking a record down with it), reproduced by this document one week after
  citing it. Plan-mode comment outcomes land in the **event log** plus
  **`plan_state` provenance** — the `mark_linked` precedent: same store, same
  shape, already carried by the state bundle.
- **A4.2** — Comments REMAIN best-effort — a failed comment never aborts a
  run, exactly as today. What changes is that the failure is a recorded fact
  (C13: not-delivered is its own state), surfaced in Run progress and
  `make explain` ("the requester was not notified — comment failed: 401").

---

## 5. Epic B — Find the work by any field

### B1. The `search` verb

**Requirement.** The Tracker port gains `search` with structured filters:
`fixversion`, `issue_type`, `component`, `label`, `status`, `text` — each
optional, ANDed; results carry key, summary, type, components, labels,
fixVersions, status.

- **B1.1** — JQL is composed **adapter-side** from the structured filters;
  the mock adapter filters its fixtures in-process so every UI and eval path
  works credential-free. Conformance-tested on both, like every verb.
- **B1.2** — `search_release` remains (it is an existing conformance surface
  and CLI contract) and SHALL be reimplemented as a `search` special case —
  one query builder, not two.
- **B1.3** — **Every filter value is escaped/validated before it touches
  JQL, and G6 is fixed in the same change** — the existing
  `fixVersion = "$1"` interpolation accepts a `"` that rewrites the query.
  Values are JQL-string-escaped; field names come from the closed filter set,
  never from input. Pinned with an injection fixture (a release named
  `x" OR key in (SEC-1)//` must arrive as a literal string, on both adapters).
  Structured-filters-only (§3.2) is what makes this provable: an escaping
  guarantee over raw JQL is a promise nobody can keep.
- **B1.4** — The filter set is **closed and defined in one module**, a
  *superset* of `ticket_fields`' vocabulary: components, labels, issue_type,
  fix_versions are shared with what the run processes (shared by
  construction, one definition), plus `status` and `text`, which are
  search-only. v1 claimed equality, which was internally false — `status`
  is discovery provenance, not a processed field, and pretending the two
  sets match is how a "supported field" quietly becomes an unprocessed one.
- **B1.5** — **Results carry `returned` and `total`, and truncation is always
  stated.** JIRA search paginates (default 50); a verb that returns a page
  as if it were the population makes every downstream count a lie — see
  B2.2, where that lie would live inside the safety confirmation. The UI
  renders "showing N of M"; fetching further pages is a UI affordance, not
  an adapter default.

### B2. The UI

- **B2.1** — The Intake fetch grows a filter row: release (as today, free
  text + autocomplete), type, component, label, status, text. Results list
  shows the attributes; each row keeps Queue / Plan-only.
- **B2.2** — **Queue filtered set** (bulk): one confirmation naming **both
  figures** — "Queue 50 of 140 matched?" — because with pagination (B1.5) the
  fetched page and the matched population differ, and a confirmation naming
  only the page is the safety feature doing the lying. Items queue
  individually through existing intake validation — a bulk path that bypasses
  per-item validation is a bulk path for bad items. Rate-limited by the
  existing queue mechanics.
- **B2.3** — A failed search says so in-view (the loadFailed convention —
  an empty result list and a failed fetch must not look alike).

### B3. Attributes on queue items

- **B3.1** — Queue items gain `{issue_type, components, labels, fix_version}`
  captured at fetch time, for queue display and filtering. Handoff only:
  **run-time processing is unchanged and pinned** — the pipeline's
  `get_item` at run start stays the source of truth, so a ticket edited
  between fetch and run is processed as it *is*, not as it was.
- **B3.2** — Existing queue items without the new fields render blank, not
  broken (the defensive-read rule every store upgrade here follows).

---

## 6. Success metrics

| # | Metric | Baseline | Target | Method |
|---|---|---|---|---|
| M1 | Comment attempts with recorded outcomes | 0% (nothing recorded) | 100% | fixture: every comment site writes a `comments` entry, incl. a forced tracker failure recording `failed` |
| M2 | Duplicate summary comments after a retry | unbounded (every retry re-posts) | 0 on update-capable adapters; bounded-with-supersession otherwise | retry fixture in `make eval` |
| M3 | Injection fixture | **fails today** (G6, verified by reading the adapter) | passes on both adapters | B1.3 pin |
| M4 | Search filter round-trip (each filter, mock adapter) | n/a | all six filters + combinations | conformance + UI fixture |
| M5 | Delivery-comment/PR-comment projection shared | two implementations would be 0% | one shared composition, pinned | A2.1 pin |
| M6 | Bulk-queue adoption, plan-comment usefulness | — | **report-only** (counts of bulk fetches, comment updates); targets set from a real quarter, not invented | queue + comments blocks |

Mock-mode figures are mechanics; nothing here claims comment *quality* — that
is what reading one on a real ticket during rollout is for.

---

## 7. Delivery plan

| Slice | Scope | Flag / knob | Exit criteria |
|---|---|---|---|
| **S1 — Search verb + escaping** | B1 (incl. **G6 fix**) | — (verb addition; G6 fix is unconditional) | `search` on both adapters, conformance green, injection fixture green, `search_release` reimplemented over it |
| **S2 — UI filters + bulk** | B2, B3 | `AIQE_TICKET_SEARCH` (default 0 until S1 conformance ships) | filter row live against mock; bulk with confirmation + per-item validation; queue attribute display; defensive reads pinned |
| **S3 — Accounting first** | A4 | **unconditional** — pure observability on comments that already post; v1 had it riding the rich flag, which would have left today's silent failures running forever in flag-off estates | `comments` block + `plan_state` provenance + events on all five existing sites; explain/Run-progress surfacing |
| **S4 — Rich comments** | A1, A2 | `AIQE_TICKET_COMMENTS_RICH` (default 0) | plan comment through `spec_store` render; delivery comment sharing `pr_comment`'s projection (pin); PR-path fused-ticket delivery comment (A2.4); plain-text floor (A2.5); bounded lengths; refusals commented |
| **S5 — Idempotency** | A3 | unconditional for the existing summary comment; rich comments inherit | markers + id persistence (A3.5) + `update_comment` capability verb + author guard (A3.2a) + fallback; `skipped_unchanged`; retry fixture green |

S1 first because G6 is a live defect and every later slice widens that surface.
S3 (accounting) precedes the rich comments deliberately: knowing whether
today's comments land is worth more than making tomorrow's prettier, and it
is the slice with zero behavioral risk.

---

## 8. Risks

| # | Risk | L | I | Mitigation |
|---|---|---|---|---|
| R1 | Comment noise erodes trust before S4 lands | M | M | S3 and S4 ship adjacent; until S4, rich comments stay flag-off in estates that retry heavily |
| R2 | JQL injection via filter values | was live (G6) | M | B1.3: closed field set, escaped values, injection fixture on both adapters, no raw JQL ever |
| R3 | JIRA comment size/format limits truncate silently | M | M | A1.2 bounded-with-statement; delivery comment links the full report (`/api/pr-coverage` pattern) |
| R4 | `update_comment` unsupported on some deployments/permissions | M | L | capability verb + stated supersession fallback (A3.2) — the LLM adapters' `tool_policy` pattern, not an assumption |
| R5 | Bulk queue of a broad filter floods the queue/budget | M | M | confirmation names the count; per-item intake validation; envelope warnings already fire per key |
| R6 | Comment content leaks internals to a broadly-visible ticket | L | M | comments built only from the same projections the run record exposes; no paths beyond repo-relative test files; secrets already gate-scanned before any content exists to cite |

---

## 9. Open questions

| # | Question | Owner | Needed by |
|---|---|---|---|
| Q1 | Comment formatting: Jira Cloud v3 wants ADF for rich comments; Server/DC takes wiki markup. **Narrowed in v2:** the plain-text floor is now AC A2.5, not an option — the remaining question is only whether S4 also ships ADF/wiki rendering as adapter capability, or defers it | EM | S4 |
| Q2 | Should the delivery comment @-mention the reporter/assignee? (Notification value vs. noise; JIRA already notifies watchers of comments) | Product + LEAD | S3 |
| Q3 | Saved filter presets in the UI — worth persisting, and where (they are per-user, and the platform has no per-user store)? | Product | after S2 usage data |
| Q4 | Should `needs_follow_up` from selective approval also comment the ticket (the reviewer excluded a committed test)? It is the one message the requester most needs and currently lives only in the approved-artifact manifest | Product | S3 |

---

## 10. Constraints (inherited, non-negotiable)

Ticket text is data, never instructions — including replies to our own
comments. The gate remains the only writer to any repo; comments are
observability, not control. Comments stay best-effort (never abort a run) with
outcomes now recorded (C13: `posted`/`failed`/`skipped_unchanged` are distinct
states). The port boundary holds: all JIRA traffic through the Tracker port,
JQL composed adapter-side, conformance-tested, mock-first. The iron rule
covers every cost figure a comment renders.

---

## Appendix A — Worked example

`LEAD` fetches: type `Bug`, component `checkout`, release `2.14` → 6 tickets.
Queues 4 (one confirmation), one as Plan-only.

On PROJ-410 (the Plan-only one), after authoring:

> **AI-QE test plan for PROJ-410** *(plan comment, marker `aiqe:plan:PROJ-410`)*
> 3 scenarios proposed — awaiting approval (`make plan-approve KEY=PROJ-410`):
> - `PROJ-410-S1` — refund rejected above captured amount *(api → e2e-api-tests-1)*
> - `PROJ-410-S2` — partial refund emits `refund.partial` *(api)*
> - `PROJ-410-S3` — refund audit row visible in admin *(ui — added by adversarial review)*
> Full plan: attached (pdf) · plan editor link

After approval and generation, the *same* comment thread:

> **AI-QE delivered tests for PROJ-410** *(delivery comment — updated in place on the retry, not re-posted)*
> **Tests:** 2 created · 1 updated — branch `test/PROJ-410-ai-qe` @ e2e-api-tests-1 ✅ committed
> - `suites/refunds/PROJ-410-refund-cap.spec.js` *(S1)* · validation ✅
> - `suites/refunds/PROJ-410-partial-event.spec.js` *(S2)* · validation ✅
> - `suites/admin/refund-audit.spec.js` *(S3, extended)* · validation ✅
> Reviewer: approve (0 unresolved) · Critic: 0.88 (advisory) · Cost: ~$0.41 (estimated)

And in the run record, for the audit that follows any incident:
`comments: [{kind: plan, outcome: posted}, {kind: delivery, outcome: updated}]`.

---

## Appendix B — Revision history

**v2 (2026-08-06)** — after an adversarial gap review of v1, ten findings. The
two largest made v1 unimplementable as written, and one of them reproduced a
defect class this document series had named seven days — one document — earlier.

| Change | Driven by |
|---|---|
| A3.5: comment-id persistence decided (plan_state for plan comments, run-record `comments` blocks for delivery; `find_comment` verb considered and rejected with reasons) | Finding 1 — v1 specified update semantics with **no way to locate the comment to update**: no id store, no list verb. S4 would have stalled at design |
| A4.1a: plan-mode outcomes land in the event log + plan_state provenance | Finding 2 — v1 put outcomes in "the run record", and the plan comment is posted by plan mode, **which writes no run record by design**. The cost PRD's G1 shape, reproduced by this document one week after citing it |
| S3 (accounting) unconditional and re-ordered before rich comments; idempotency covers the existing summary comment | Finding 3 — v1 had A4 riding the rich-comments flag, leaving today's silent failures and retry spam running forever in flag-off estates |
| B1.5 `returned`/`total` contract; B2.2 confirmation names both figures | Finding 4 — JIRA search paginates; a confirmation naming the fetched page as the population is the safety feature doing the lying |
| A3.2a: update only own-account comments; mismatch → append + recorded | Finding 5 — a forged or coincidental marker in a third party's comment must never let the platform edit a human's words |
| A2.4: PR-mode fused runs comment the discovered ticket (resolves fused-PRD E5, closed there with a pointer here) | Finding 6 — plan-from-PR delivery already routed to the ticket; plain pr-mode fused runs left the requester unheard |
| A3.1: marker is a visible footer, not "invisible" | Finding 7 — JIRA plain text has no hidden markup; the footer doubles as run attribution |
| B1.4: filter set is a stated *superset* of `ticket_fields`' vocabulary | Finding 8 — v1 claimed equality while including `status`/`text`, which the run does not process |
| A1.2: `comments.max_chars` org-config knob, default 8,000, ceiling named | Finding 9 |
| A2.5: plain-text floor as an AC; Q1 narrowed to the rich-format question | Finding 10 |
