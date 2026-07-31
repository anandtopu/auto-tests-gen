# Spec-driven development — user-story backlog

> **STATUS: ALL 8 BUILD SLICES SHIPPED** (2026-07-31, commits `7d42fc6`…HEAD).
> Both gates ship conservatively: `spec.requirements_gate: off` and
> `spec.enforce: off` (strict proven live — refuse-then-waive — but default-on
> is a team decision after a warn-mode sprint). Adversarial UAT: REVIEW.md
> Pass 9. Story 5.2 is satisfied by the existing eval mechanism (fixtures'
> `expected`/`expected_context` ARE the executable clauses; the scorecard
> prints retention) — no separate schema was needed.

Make **specifications the primary artifact** of the platform: formal, versioned,
machine-verifiable specs that humans approve and every downstream step
(generation, gate, trace, drift) provably satisfies — instead of free-form
markdown that generation merely *interprets*.

## Where SDD applies — the scope review

The platform is already **halfway to spec-driven** (this backlog upgrades, it
does not reinvent):

| Exists today (keep) | The SDD gap it leaves |
|---|---|
| `analyze` emits `behaviors` (B1, B2…) from the ticket | behaviors are informal strings; nobody approves the REQUIREMENTS before planning starts; `open_questions` don't block anything |
| Test plans carry scenario ids (`KEY-Sn`), layer, target repo, `behavior_ref`, `data_needs` | the plan the human signs is FREE-FORM markdown; the structured contract is a side-product with a `required`-only schema; scenarios have no Given/When/Then, so generation interprets titles |
| Approval snapshots the signed text; edits diff against the baseline | the signature covers bytes, not semantics — a re-approver diffs prose, not scenario-level changes |
| The gate enforces born-mapped (catalog sidecar per spec file) | nothing enforces **born-specified**: a generated test claims a `scenario_id`, but a test satisfying no scenario, or an approved scenario with no test, passes the gate silently (the trace matrix only *reports* the latter) |
| The trace matrix joins ticket → scenario → spec → gate → CI | the requirement end is open: scenarios link to `behavior_ref`s that trace to nothing a human ever approved |
| `coverage_drift` alarms when a repo's uncovered surface grows | an **app-contract change** (OpenAPI/route diff) never marks the dependent scenarios/tests STALE — the spec silently rots against the system it describes |
| The non-negotiables are prose in CLAUDE.md + ~30 test pins | the platform's own constitution is not a machine-readable spec a CI job verifies as a set |

**Prior art adopted / rejected:** EARS requirement syntax (adopted — structured,
LLM-friendly, human-readable); spec→plan→tasks staged gates à la Spec Kit / Kiro
(adopted — maps 1:1 onto analyze→testplan→generate, which already stop for
humans); Gherkin as the scenario step format (adopted for *steps only* —
Given/When/Then inside our scenario schema, NOT `.feature` files or a
runner-coupled DSL); full BDD frameworks (rejected — the gate runs each repo's
own framework, and a new runner violates "never introduce a new approach");
spec-as-code DSLs (rejected — YAML + JSON-schema is the platform's native
tongue and diffs cleanly for the human gate).

**Personas** as ever: Dev, QA, Lead, EM, Op.

**Ground rules every story inherits:**
- A spec is DATA to phases, never instructions — same framing as tickets/chunks.
- Humans sign specs; machines verify satisfaction. No story may auto-approve.
- The markdown the reviewer reads is **rendered from** the structured spec —
  one source of truth, never two files to keep in sync by hand.
- Back-compat: estates with only free-form plans keep working; SDD arrives
  behind flags, mock-first, with the usual per-mechanism kill switch.
- The gate stays the only writer; spec enforcement adds CHECKS, not writers.

Sizing S/M/L as before.

---

## Epic 1 — The specification artifact (scenario specs)

### 1.1 Structured scenario spec + schema — **M**
**As a** QA, **I want** each ticket's plan captured as a schema-validated spec
(`specs/<KEY>/testplan.yaml`) — scenarios with id, title, layer, target repo,
`requirement_refs`, Given/When/Then steps, data contract refs, and
`verification` (what a satisfying test MUST assert) — **so that** what I
approve is precise enough for a machine to check satisfaction against.
**AC:** JSON-schema (`engine/phases/contracts/spec.schema.json`, full
properties, not required-only) validates every spec; the testplan phase emits
it (prompt upgrade); mock emits a fixture spec; schema violations fail the
phase like any contract violation.

### 1.2 Markdown is a rendering, not a source — **M**
**As a** Lead, **I want** `testplans/<KEY>.md` generated FROM the spec by a
deterministic renderer, **so that** the reviewer's document and the machine's
spec can never disagree.
**AC:** `spec_render.py` (spec → md, byte-deterministic); plan editor saves
round-trip through the spec (edit md → parse back or edit structured fields);
existing free-form plans remain valid (a spec-less plan renders/behaves exactly
as today — pinned).

### 1.3 Approval signs the spec — **S**
**As a** Lead, **I want** approval to hash and snapshot the STRUCTURED spec
(reusing `plan_state` versioning), with diff-since-approval computed at
scenario level ("S2 steps changed, S4 added") not line level, **so that**
re-approval reviews semantic change.
**AC:** spec sha256 on the approval history entry; `diff_since_approval` gains
a structured mode; the adversary/arbiter operate on the spec (they already only
ADD — now provably, by schema).

## Epic 2 — Requirements specs (the stage before planning)

### 2.1 EARS requirements from the ticket — **M**
**As a** QA, **I want** the analyze phase to formalize the ticket into
`specs/<KEY>/requirements.yaml` — EARS-shaped statements ("WHEN a discount
exceeds the cap, THE SYSTEM SHALL reject with 422") each with an id (R1…),
source (which AC/comment), and `ambiguities` — **so that** scenarios trace to
requirements a human actually validated, closing the trace matrix's open end.
**AC:** schema'd like 1.1; `behavior_ref` becomes `requirement_refs`
(back-compat mapping B*→R*); ambiguities render in the plan editor.

### 2.2 Optional requirements gate — **M**
**As a** Lead, **I want** an opt-in `pipeline.sh requirements <KEY>` stop
(org-config `spec.requirements_gate: off|on`), mirroring plan-first, **so
that** on high-stakes tickets a human confirms WHAT before the platform plans
HOW.
**AC:** same state machine as plans (draft/approved via `plan_state`-style
store); plan mode consumes approved requirements when present; OFF = today's
flow byte-for-byte (pinned).

### 2.3 Blocking clarifications — **S**
**As a** Dev, **I want** requirements marked `blocking: true` (contradictory
ACs, undefined behavior) to stop the chain with a ticket comment asking the
question, **so that** the platform asks instead of guessing — extending the
resolver's `needs_clarification` pattern to requirements.
**AC:** non-blocking ambiguities flow through as today; blocking ones stop
before testplan with an actionable comment; pinned both ways.

## Epic 3 — Spec satisfaction (generation + gate)

### 3.1 Generation consumes the spec — **M**
**As a** QA, **I want** the generate phase to receive the structured spec and
stamp each test with its `scenario_id` AND assert what the scenario's
`verification` names, **so that** "covered" means verified-as-specified, not
merely name-matched.
**AC:** generate prompt upgrade; contract `tests[]` unchanged (scenario_id
already exists); critic gains a `spec-mismatch` finding kind (advisory, as
ever).

### 3.2 Born-specified gate check — **L**
**As a** Lead, **I want** a gate check (strict mode, org-config
`spec.enforce: off|warn|strict`) that every changed spec file's `scenario_id`
resolves to an APPROVED scenario, and every approved scenario is covered or
carries a **waiver**, **so that** the signed spec is enforced, not just
reported.
**AC:** new gate exit code (8) with its own adversarial test; `warn` mode
prints, `strict` refuses; waivers (`specs/<KEY>/waivers.yaml`: scenario id,
reason, who, expiry) are the escape hatch and render in the trace matrix;
default `off` — enforcement is a team decision. PR-path tests (no plan) are
exempt by construction.

### 3.3 Trace matrix closes the loop — **S**
**As an** EM, **I want** trace rows to run requirement → scenario → test →
gate → CI with waivers visible, **so that** an audit answers "which approved
requirement is unverified?" in one filter.
**AC:** `requirement_refs` + `waiver` columns; the "approved scenario with no
test" row distinguishes *waived* from *missing*.

## Epic 4 — Spec drift (the spec vs the living system)

### 4.1 Contract-diff staleness — **M**
**As a** QA, **I want** an app repo's contract change (OpenAPI/route diff the
platform already harvests) to mark dependent scenarios `stale` (matched via
the same endpoint-normalization the extend scout uses), **so that** specs age
loudly instead of silently.
**AC:** `spec_drift.py` in `make maintain`; stale scenarios flag in the plan
editor + trace matrix; a stale APPROVED spec notifies (Notify port); staleness
never auto-edits a signed spec — a human re-approves or waives.

### 4.2 Re-verification runs — **S**
**As a** Lead, **I want** `make spec-verify KEY=..` to re-run validate against
the existing tests for a stale spec's scenarios, **so that** "still passing"
vs "actually broken by the contract change" is one command.
**AC:** read-only (no generation, no gate); result attached to the spec state.

## Epic 5 — The platform's own constitution

### 5.1 Machine-readable constitution — **M**
**As an** Op, **I want** the non-negotiables captured as
`specs/platform/constitution.yaml` (statement, category, enforcing test ids),
verified by a CI check that every clause names ≥1 existing, passing test pin,
**so that** the platform's own spec is executable, and a deleted pin breaks
the build, not the promise.
**AC:** `test_constitution.py` cross-references clause→pin existence; CLAUDE.md
non-negotiables section states it is the RENDERING of the constitution file.

### 5.2 Eval fixtures as executable feature specs — **S**
**As a** QA, **I want** benchmark fixtures to declare `spec:` blocks (given
trigger X, the platform SHALL resolve/produce Y — already implicit in
`expected`), formalized under the same schema discipline, **so that**
`make eval` is the platform verifying its own specs.
**AC:** fixture schema documented; `expected_context` (7.2) folds in as one
clause type; scorecard line names spec-clause pass rate.

## Epic 6 — Spec lifecycle UX

### 6.1 Spec view in the plan editor — **M**
**As a** Lead, **I want** the plan editor to show the STRUCTURE (requirements
list, scenario cards with GWT steps, waivers, staleness flags) beside the
rendered markdown, **so that** review happens at the level the machine
enforces.
**AC:** builds on `#plan-editor`; scenario-level diff-since-approval (1.3)
renders here; adversary verdicts attach to scenario cards.

### 6.2 Spec exports + publishing — **S**
**As a** QA, **I want** export/attach/publish (already built for plans) to
carry the spec rendering with requirement/waiver tables, **so that** the
signed artifact circulating in Confluence/JIRA is the real spec.
**AC:** `export_plan` consumes the renderer; no second export path.

### 6.3 Spec reuse rides the existing rails — **S**
**As a** QA, **I want** plan reuse (3.3) and the knowledge chunks to operate on
structured specs (scenario chunks per scenario, not per plan), **so that**
retrieval and reuse get sharper as specs get more precise.
**AC:** `knowledge_chunks` emits per-scenario chunks when a structured spec
exists; `plan_reuse` adaptation re-stamps at scenario granularity; TF-IDF/mock
fallbacks unchanged.

---

## Sequencing

```
Phase A (the artifact):    1.1 → 1.2 → 1.3        | 5.1 (independent)
Phase B (requirements):    2.1 → 2.2 → 2.3        | 5.2
Phase C (enforcement):     3.1 → 3.2 → 3.3        | 6.1
Phase D (drift + polish):  4.1 → 4.2 | 6.2, 6.3
```

Hard rules: 3.2 (`spec.enforce`) defaults `off` and cannot flip to `strict`
before 3.1 + 1.x land and a real estate runs `warn` for a sprint; 2.2's gate
defaults `off`; every stage is mock-demoable. Design and module-level
architecture: [spec-driven-architecture.md](spec-driven-architecture.md).
