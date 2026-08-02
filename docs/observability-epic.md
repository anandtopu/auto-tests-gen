# Observability epic — transaction logging, tracking, alerting, notifications

**Goal.** A user can see every request and transaction the platform handled, and
configure alerts and notifications about them, from the UI.

This document is the review, the design, and the backlog. It is written so the
stories can be built in slices that each ship something usable, in the same
style as `docs/multi-llm-providers.md` and `docs/cost-reduction-architecture.md`.

---

## Part 1 — What exists today (reviewed, with evidence)

The platform is not unobservable. It already has real surfaces:

| surface | what it covers |
|---|---|
| Run records | `reports/runs/<RUN_ID>.json` — per-phase contracts, per-repo gate status/exit/commit |
| Gate diffs | `reports/runs/<RUN_ID>-<repo>.diff` — the durable copy of generated code |
| Cost ledger | `out/cost.tsv` → run-record `spend` blocks → `make cost-report` |
| Review state | `reports/runs/reviews.json` — status per key, with the deciding actor |
| Plan history | `plan_state` records `by` + timestamp on every approval and edit |
| OpenHands traces | `reports/openhands/` — conversation launches and webhook events |
| Coverage/spec drift | `coverage_drift.py`, `spec_drift.py` — notify when coverage regresses |
| Notify port | Slack + Email adapters, `NOTIFY_KIND=slack\|email\|both` |
| Telemetry port | `adapters/telemetry/splunk.sh`, verb `emit_event` |
| Team report | `make report` — throughput, backlog, estate health |

**So the gap is not "no data". It is that the data is per-domain, per-run, and
mostly write-only.** Four specific findings, each verified in the source:

**F1 — HTTP requests are discarded on purpose.**
`bin/dashboard_server.py:146` overrides `log_message` with `pass` ("quiet
request log"). Nothing records that a request arrived, who made it, or what it
did. **34 distinct POST endpoints** mutate state — approving plans, editing the
registry, writing `.env`, queueing runs, launching paid OpenHands conversations
— and none of them leaves a trace that survives the process.

**F2 — Telemetry fires once per run, at the very end.**
`TELEM emit_event` appears at exactly one call site (`engine/pipeline.sh:622`),
piping the finished run record. A run that aborts at exit 77, is refused by the
gate, or dies mid-phase emits nothing. The telemetry stream therefore
systematically under-reports failure — the opposite of what it is for.

**F3 — Notifications are fire-and-forget and unconfigurable.**
`NOTIFY post` has six call sites, all in `pipeline.sh`, all hard-coded decisions
about what is worth telling someone. There is no record that a notification was
sent, no way for a user to change what triggers one, and no way to see what they
missed. `NOTIFY_KIND` is a deployment-wide env var, not a user preference.

**F4 — Actor attribution exists but is scattered.**
`plan_state` and `review_state` record `by` on decisions. Nothing else does, and
there is no way to ask "what did this person change last week?" or "who queued
the run that committed this?" across domains.

**What that costs.** The platform pushes commits to real repositories, spends
real money on LLM calls, and starts conversations on accounts we cannot meter.
Today, if someone asks "who approved this plan and what ran because of it?", the
answer has to be reconstructed from four files by someone who knows the layout.

---

## Part 2 — Design principles

These are constraints this codebase already enforces. The epic inherits them;
they are listed because breaking one is how the feature becomes a liability.

1. **Ports and adapters.** Alert delivery goes through the Notify port. The
   engine never imports a vendor SDK. A new channel is a new adapter.
2. **The event log is append-only and never load-bearing.** A run must not fail
   because logging failed. Emission is best-effort and its failure is itself
   recorded, never raised into the pipeline.
3. **Never invent data.** The same iron rule as the cost stack: a figure that
   was not measured is labelled, not defaulted to zero. An alert that could not
   be evaluated says so rather than reporting "healthy".
4. **Secrets never reach the log.** Settings writes `.env`; the event for that
   transaction records *which keys changed*, never their values. Redaction is
   tested adversarially, not assumed.
5. **Observed content is data, not instructions.** Event details rendered in the
   UI are escaped; details that originate from ticket or PR text carry the same
   framing the prompts use.
6. **State mutations stay inside `fs_lock`.** The event log is append-only,
   which makes it the one store where concurrent writers are safe — but the
   index built from it is not, and goes through the lock like everything else.
7. **Relocatable.** Paths resolve through `engine/lib/app_paths.py` (R12), so
   the log works under `readOnlyRootFilesystem`.
8. **Mock mode works.** `AIQE_MOCK=1` must exercise the whole chain without
   sending anything externally, exactly as the email adapter already does.
9. **Retention is bounded and explicit.** An append-only log grows forever; the
   epic ships pruning in the same slice that ships writing, not later.

---

## Part 3 — The event model

One record shape for every transaction, because the value of this feature is
answering questions *across* domains.

```jsonc
{
  "id":        "evt_01J8...",        // ULID-like, sortable by time
  "ts":        "2026-08-01T12:34:56Z",
  "kind":      "plan.approved",       // <domain>.<past-tense verb>
  "actor":     "anand",               // who; "system" for scheduled work
  "source":    "ui",                  // ui | cli | webhook | pipeline | cron
  "target":    "PROJ-301",            // the key/repo/PR this concerns
  "run_id":    "1785612364-10233",    // correlation — nullable
  "outcome":   "ok",                  // ok | refused | failed | degraded
  "detail":    {"scenarios": 7},      // small, structured, NEVER secrets
  "duration_ms": 412
}
```

`kind` is a closed vocabulary so the UI can filter and alert rules can match
without regex guesswork. Initial set:

| domain | kinds |
|---|---|
| `request` | `received`, `refused` (401/403), `failed` (5xx) |
| `run` | `queued`, `started`, `phase_completed`, `aborted`, `completed` |
| `gate` | `committed`, `refused`, `no_changes` |
| `plan` | `authored`, `edited`, `approved`, `revoked` |
| `spec` | `requirements_approved`, `drift_detected` |
| `registry` | `repo_added`, `repo_removed`, `mapping_changed` |
| `settings` | `changed` (key names only) |
| `spend` | `phase_metered`, `budget_warned`, `budget_aborted` |
| `notify` | `sent`, `failed` |
| `alert` | `fired`, `resolved` |

**Storage.** `reports/events/YYYY-MM-DD.jsonl`, append-only, one line per event
— under `reports/`, which is already a mounted volume in every deployment.
**Slice 2 decision: no SQLite index yet.** The original design called for
`reports/events.db` backing UI queries. Building it now would add a derived
store, a rebuild path, corruption handling and a staleness window — for a
corpus that is one JSONL file per day, capped at `retain_days`, scanned
newest-first with early exit. At demo-estate volume a filtered query over 30
day-files completes in milliseconds.

So the index is DEFERRED until there is a measured reason for it, and the
trigger is written down rather than left to taste: build it when a filtered
`/api/events` query exceeds ~300 ms, or when retention is raised past 90 days.
The JSONL stays the source of truth either way, so adding the index later is
additive and changes no caller. This follows the same rule as the vector index —
derived data is regenerated, never repaired — and the same instinct as the rest
of the platform: do not ship a cache before the thing being cached is slow.

---

## Part 4 — Epics and stories

### E1 — Transaction log (the substrate)

* **1.1** As an operator, every state-changing API request is recorded with
  actor, endpoint, outcome and duration, so I can see what was done and by whom.
  *AC:* all 34 POST endpoints emit; a GET does not; a 401 emits
  `request.refused`; the request body is NOT stored.
* **1.2** As an operator, pipeline runs emit lifecycle events at start, per
  phase, and at every exit path — including abort and gate refusal — so failures
  are as visible as successes (closes F2).
  *AC:* an exit-77 abort and a gate exit-2 both leave events; killing the
  process mid-run leaves the events already written.
* **1.3** As a maintainer, emission never breaks a run.
  *AC:* an unwritable event dir, a full disk, and a corrupt log line each leave
  the pipeline's exit code unchanged; the failure is reported once, not per event.
* **1.4** As an operator, the log is pruned on a retention I set.
  *AC:* `make maintain` drops files older than `observability.retain_days`;
  the prune is logged as an event itself.

### E2 — Activity view (tracking)

* **2.1** As a QE lead, I can browse all transactions in the dashboard, newest
  first, with filters for actor, kind, target, outcome and time range.
* **2.2** As a QE lead, I can pivot from any run, plan or repo to its timeline,
  so "what happened to PROJ-301" is one click, not four files (closes F4).
* **2.3** As an auditor, I can export a filtered range as CSV/JSON.
  *AC:* the export carries no secrets and is bounded in size.

### E3 — Alert rules (configurable, from the UI)

* **3.1** As an operator, I can define alert rules over the event stream —
  match on kind/outcome/target, with a threshold and a window ("3 gate refusals
  for one repo in an hour").
* **3.2** As an operator, I can enable, disable and test a rule without editing
  a file. *AC:* "Test" sends through the real channel and is itself recorded as
  `notify.sent`, so a silent misconfiguration is visible.
* **3.3** As an operator, a firing rule resolves when the condition clears, and
  re-fires no more than once per cooldown.
  *AC:* a flapping condition produces one notification, not fifty.
* **3.4** As a maintainer, a rule that cannot be evaluated reports
  `unevaluable` naming why — never "healthy" (principle 3).

### E4 — Notification routing

* **4.1** As an operator, I choose the channel per rule (Slack, email, both),
  and the recipients for email rules, from the UI.
* **4.2** As an operator, every notification attempt is recorded with its
  outcome, so I can tell "nothing happened" from "we failed to tell you"
  (closes F3).
* **4.3** As an operator, I get a digest option instead of per-event noise.
* **4.4** As a maintainer, delivery failure is retried with backoff and then
  recorded as `notify.failed`; it never aborts the work that triggered it.

### E5 — Surfacing and docs

* **5.1** Overview tile: recent activity + firing alerts.
* **5.2** `bin/qa.py events` / `alerts` CLI parity with the UI.
* **5.3** `docs/user-guide.md`, `docs/use-cases.md` and `docs/architecture.md`
  updated; a new diagram for the event → rule → channel chain.

### E6 — Hardening (runs against every slice, not after)

* **6.1** Adversarial suite `tests/observability-adversarial.sh`: secret in a
  settings change must not reach the log; a crafted `target` must not break the
  UI render; an unwritable log must not fail a run; a flapping rule must not
  storm; a rule matching everything must not wedge the evaluator.
* **6.2** Unit + end-to-end coverage per slice, pinned like the rest of the suite.

---

## Part 5 — Slice plan

Each slice ends green (full suite + both demos) and is pushed.

| slice | stories | ships |
|---|---|---|
| 1 | 1.1–1.4 | `event_log.py`, emission from server + pipeline, pruning, pins |
| 2 | 2.1–2.3 | Activity view, filters, per-entity timeline, export |
| 3 | 3.1–3.4 | Rule store + evaluator, UI editor, test-fire, cooldown |
| 4 | 4.1–4.4 | Routing, delivery recording, digests, retry |
| 5 | 5.1–5.3 | Overview tile, CLI, docs + diagram |
| 6 | 6.1–6.2 | Adversarial suite, multi-pass review, fixes |

**Open question for the user, deliberately not assumed:** the platform has no
user accounts — `actor` today would come from the UI token or `$USER`. Slice 1
records whatever identity is available and labels it honestly
(`actor_source: token|env|unknown`) rather than inventing one. If per-user
attribution matters for audit, that is an authentication story and should be
scoped separately rather than faked here.
