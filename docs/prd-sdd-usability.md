# PRD — Spec-driven development people can actually follow

|  |  |
|---|---|
| **Status** | Draft v2 — revised after adversarial gap review (Appendix B) |
| **Author** | Product Management (QE Platform) |
| **Date** | 2026-08-06 |
| **Doc** | `docs/prd-sdd-usability.md` |
| **Related** | [sdd-for-e2e-adoption.md](sdd-for-e2e-adoption.md) · [spec-driven-architecture.md](spec-driven-architecture.md) · [ui-guide.md](ui-guide.md) (Spec workflow view) · architecture §5.18 |

**The ask:** end users cannot understand how spec-driven development works or
how this application uses it. Make it meaningful, intuitive, easy to learn,
easy to use in regular workflows.

**The honest framing:** the machinery is built and sound — signed specs,
EARS requirements, a gate check, drift watching, waivers, a governance page, a
six-state workflow model, savings counting. Eight slices of it. **This PRD adds
almost no engine capability on purpose** (the same discipline
`sdd-for-e2e-adoption.md` Part 6 already follows): the problem is not what SDD
does, it is that the product asks a newcomer to learn roughly fourteen terms,
three independently-moving knobs, eight `make` targets — and one word that
means two different things in the same screen. Confusion this consistent is
not a training problem; it is a product surface problem, and it is fixable
without touching what works.

---

## 1. The diagnosis — where the confusion actually comes from (verified)

| # | Source of confusion | Evidence |
|---|---|---|
| **D1** | **"Spec" means two things in one product.** The signed plan document (`specs/<KEY>/testplan.yaml`, "the spec of record") and the generated test files (`*.spec.js`, "changed specs", "generated specs") share one word. A QA engineer's prior is the *test-file* meaning; every SDD sentence they read fights it | the gate "executes changed specs"; the view is "Spec workflow"; the files are `.spec.js` — all three in today's UI/doc copy |
| **D2** | **No glossary exists.** Fourteen-plus terms (spec, requirements, EARS, signed, superseded, waiver, drift, stale, constitution, governance, enforce, warn, strict, scenario, born-mapped) with no in-product definition anywhere | `grep -rli glossary docs/ bin/ engine/` → nothing |
| **D3** | **Adoption is a three-knob composition puzzle.** `AIQE_SPEC_MODE` (env), `spec.requirements_gate` (org-config), `spec.enforce: off\|warn\|strict` (org-config) move independently; understanding "what is SDD doing for me right now" requires composing all three, plus `AIQE_REQUIREMENTS_REAUTHOR` for one edge. The Settings copy is honest per-knob — the *composition* is the user's homework | `org-config.yaml:156,161`; Settings governance section |
| **D4** | **The journey spans ~8 commands with no single guided path.** `requirements` → `requirements-approve` → `plan` → `plan-show/edit` → `plan-approve` → `plan-tests` → `spec-verify`, plus `select`/waivers — and the **wizard ladder has no requirements step**: with the requirements gate on, a user's journey stops at a step the journey view does not show | `wizard_status.py:79–107` — the ladder starts at "Author the test plan" |
| **D5** | **Machine state names leak — and they name the *pending* stage, which reads as the *achieved* one.** The six states render raw (`dashboard.py:1965–1970`: trail dots with the machine name in a `title=` attribute, `escHtml(r.state)` below — no label map exists anywhere). Worse than opacity: each state names the milestone being *worked toward* (`spec_workflow.py:182–198`: state `plan` fires when **no plan exists yet**; `approved` when the plan is **awaiting approval**; `committed` when there is **no gate commit recorded**) — so a reader's natural reading of every middle state is one stage too optimistic | verified against both files; the v2 review caught this PRD's own v1 label table making exactly that misreading — see Appendix B |
| **D6** | **The benefit is invisible at the moment of use.** Approving (signing) a plan arms drift-watching, scenario-level change review, and gate enforcement — none of which the approval moment mentions. Users are asked to perform a ceremony whose payoff lives in a document they have not read | approval flows show status changes only |
| **D7** | **Refusals teach unevenly.** The CLI refusals name fixes (`plan-approve`, exit-65 comments, exit-8 waiver path); the UI shows some of this per view, but there is no contract that *every* SDD refusal carries its one next action | message-content coverage is untested; some views chip, some toast |

None of this says the model is wrong. Six states, human sign-off, honest
enforcement modes — that is the right skeleton. It is dressed in internals.

---

## 2. Users

| Persona | Today | After |
|---|---|---|
| **QA engineer, week one** | opens "Spec workflow", reads six machine words and a governance card, closes it | reads one sentence per state, sees where their ticket is and the one button that moves it |
| **QA lead adopting SDD** | derives a rollout from three knobs and two docs | picks one of four adoption levels written in consequence language |
| **Requester (DEV/PO)** | hears "the spec isn't signed" as jargon | the ticket comment and the view say "the test plan is waiting for your approval — here" |

---

## 3. Goals and non-goals

### 3.1 Goals

1. A newcomer can explain SDD's loop after one sitting with the product —
   *plan proposed → human approves (signs) → tests generated → gate holds
   generation to the approved plan → drift watched* — without reading a doc.
2. One journey surface shows every state in plain language with exactly one
   next action.
3. Adoption is **one decision** (a named level), not three knobs.
4. Every SDD refusal teaches: why, and the one action, at the refusal site.
5. One vocabulary, defined in-product, with the spec/spec-file collision
   resolved in user-facing copy.

### 3.2 Non-goals

- **Not** renaming internal state names, file paths, APIs, env vars, or make
  targets. `STATES`, `specs/<KEY>/`, `spec.enforce` are load-bearing, pinned,
  and scripted against. This is a **label layer**: machine names stay
  authoritative and visible-on-demand; plain language goes on top.
- **Not** hiding states or steps to look simple. Simplification by omission is
  C13 with better typography — a user who cannot see the requirements gate is
  a user who cannot understand why their plan refused. Every state stays
  rendered; what changes is that each explains itself.
- **Not** new governance semantics. The presets (Epic C) compose *existing*
  knobs and nothing else — pinned.
- **Not** auto-adoption or nudging estates up levels. Adoption is a team
  decision; the product's job is making the decision legible.

---

## 4. Epic A — One vocabulary

### A1. The term policy

**Requirement.** User-facing copy (UI, ticket comments, refusal messages,
user-guide/use-cases docs) SHALL follow one term policy:

| Concept | The word | Never |
|---|---|---|
| The reviewable/signable artifact | **test plan** | "spec", "spec of record" in UI copy |
| A structured plan a human approved (signature exists) | **approved test plan (signed)** | — |
| A legacy prose plan a human approved (no signature possible) | **approved test plan (prose — not signed)** | bare "approved test plan", which would claim the signed guarantees |
| Generated test files | **tests** / **test files** | "specs", "generated specs" |
| `requirements.yaml` | **acceptance criteria (EARS)** | bare "requirements" where ticket requirements could be meant |
| `spec.enforce` behavior | **coverage enforcement** (off/warn/strict keep their names — they are honest) | — |

- **A1.0** — The three-way distinction in rows 2–3 is load-bearing, not
  pedantry: approval and signing are **different events**. Approval exists for
  every plan; signing happens only when the plan is structured — and
  enforcement, drift-watching and scenario-level diff all key on the
  *signature*. A vocabulary with one word for both states makes every
  prose-plan estate read the signed guarantees into plans that are exempt
  from all of them (§6's level descriptions inherit this, C1.0).

- **A1.1** — Internal names remain visible on demand (a ⓘ affordance shows
  the machine term and file path) — operators grep, and a label layer that
  *hides* the greppable name strands them between two vocabularies.
- **A1.2** — The policy applies to **new and touched copy**; a bulk rewrite of
  every historical doc is explicitly out of scope (churn without
  comprehension gain), but the three docs users are actually sent to are
  updated in the same slice: `ui-guide.md`, `use-cases.md`, **and
  `getting-started.md`'s "The spec-driven demo" walkthrough** — the last one
  is a newcomer's first contact with SDD, and v1 forgot it while scoping the
  docs for newcomers. All three carry currency pins and *should* fail the
  build if they drift.

### A2. The glossary — one definition file, everywhere

**Requirement.** SDD terms SHALL be defined once (`engine/lib/glossary.py` or
a data file it renders) and surfaced in-product: hover/ⓘ tooltips on every
term where it appears, and a "How this works" card on the journey view
rendering the whole loop in five sentences.

- **A2.1** — **The glossary is pinned as a coverage invariant, via term
  markup** — v1's version ("extract SDD terms from the copy and check each is
  defined") was circular: knowing which words are terms *is* the glossary, so
  that pin could never catch a new undefined term, the one case it exists
  for. The implementable mechanism: view copy references terms through
  markup (`{{term:signed}}` → label + tooltip at render time), and the pin
  asserts **both directions** — every reference resolves in the glossary,
  and every glossary entry is referenced somewhere (a defined-but-unused
  entry is documentation rot with extra steps). A lint additionally flags
  the known-ambiguous bare words (`spec`, `requirements`) appearing unmarked
  in new UI copy — the closed list of words this PRD exists to disambiguate.
- **A2.2** — Definitions are one sentence of meaning plus one of consequence
  ("**Signed** — a human approved this exact plan; generation can only
  follow it, and any later change is flagged for re-approval").

### A3. Plain-language state labels

**Requirement.** The six states render as label pairs — plain phrase first,
machine name subordinate. **Each label is derived from the state's computed
definition in `spec_workflow.py`, not authored from intuition** — because this
PRD's own v1 table authored them from intuition and got four of six wrong, all
shifted one stage optimistic (it read each state name as the milestone
*achieved*; the code fires each state for the milestone *pending*). A friendly
label that lies is worse than an opaque machine name: the machine name at
least does not claim delivery happened.

The corrected table, each row verified against the condition that fires it:

| Machine | Fires when (verbatim from the code) | Label |
|---|---|---|
| `requirements` | criteria exist, blocked or unapproved (`:168–176`) | Acceptance criteria awaiting validation |
| `plan` | **no plan exists yet** (`:182`) | Test plan being authored |
| `approved` | plan exists, **awaiting approval** (`:187`) | Plan awaiting your approval |
| `tests` | approved, tests not generated (`:192`) | Tests awaiting generation |
| `committed` | generated, **no gate commit recorded** (`:197`) | Tests awaiting delivery |
| `live` | everything satisfied, no blocker (`:202`) | Delivered — running in CI |

- **A3.1** — `spec_workflow.py` stays read-only and its API unchanged; labels
  are presentation. The state machine is correct — it was only ever mute.
- **A3.2** — Each label SHALL be pinned against its state's firing condition
  (the blocker text the module already emits), so a future state or a
  reworded condition cannot drift from its label silently — the label layer
  gets the same one-source-of-truth discipline as everything else here.

---

## 5. Epic B — One journey surface

### B1. The journey view (today's "Spec workflow", relabeled)

**Requirement.** The view SHALL show, per ticket: the six-state trail with
plain labels, the current state highlighted, **the specific blocker when
blocked, and exactly one next action** — as a button where the action is a UI
action, always with the equivalent command shown (`make plan-approve
KEY=PROJ-301`), because the CLI is a feature, not an implementation detail.

- **B1.1** — `spec_workflow.py` already computes blocker/next-command/owner
  per ticket; this epic **renders what is computed** — the gap is surface,
  not data, and a pin asserts the view derives from `spec_workflow` output
  rather than re-inferring (the wizard's plan-coherence lesson).
- **B1.2** — The nav entry is relabeled to name the journey (working title:
  "Plan → tests journey"; final name is open question Q2). The `data-view`
  id stays `specflow` — nav and view-doc pins are updated in the same change,
  which the docs-currency pins force anyway.

### B2. The wizard learns the whole journey

**Requirement.** WHEN the requirements gate is on, the wizard ladder SHALL
include the acceptance-criteria step — status, why it blocks planning, and
its approve action — so the journey's first stop is visible in the view built
to show journeys (D4: today the ladder starts at plan authoring, and a
requirements-gated user is blocked at a step that does not exist on screen).

- **B2.1** — With the gate off, the step does not render (a permanently-done
  step for a gate an estate never enabled is noise teaching nothing).
- **B2.2** — Ladder labels remain stable across states (the existing rule).

### B3. The refusal contract

**Requirement.** Every SDD refusal SHALL carry, at the site the user sees it:
what refused, why, and the one action — as a **tested message contract**
(fixture per refusal, asserting the action text), covering: requirements gate
(plan refused → validate/approve criteria), plan approval gate (generation
refused → approve the plan), coverage enforcement exit 8 (which scenario,
cover-or-waive with the waiver path), expired waiver (renew-or-cover, naming
expiry), drift-stale scenario (re-approve or retire, naming the vanished
surface).

- **B3.1** — CLI and UI render the same contract text — **one message builder
  with a named home**: a Python module (beside `EXIT_MEANINGS` and
  `work_queue.failure_reason`, the two precedents for exactly this job),
  which bash calls the way `pipeline.sh` already calls `ticket_fields.py`,
  and the UI fetches. v1 asserted "one builder" across bash, Python and JS
  surfaces without saying where it lives — an architectural claim with no
  address is a wish.

### B4. The moment of benefit

**Requirement.** WHEN a plan is approved, the confirmation SHALL say what the
approval **actually did** — derived from the plan's signed/prose status, never
recited from a template:

- A **structured** plan: signed — change-review at scenario level, drift
  watching armed, and (when enforcement is on) generation now held to this
  plan, one line each.
- A **prose** plan: approved, *not signed* — generation may proceed, and the
  confirmation says plainly that drift watching and enforcement do not apply
  to prose plans, with the one-line pointer to what a structured plan adds.

(D6: the ceremony finally states its payoff at the moment it is performed —
and v1 would have had the prose-plan confirmation recite guarantees the plan
is exempt from, which is the friendly lie this PRD exists to eliminate,
delivered at the most trusted moment in the workflow.)

---

## 6. Epic C — Adoption in one decision

### C1. Adoption levels

**Requirement.** Settings SHALL offer four named levels, each one sentence of
consequence, mapping **deterministically to existing knobs and nothing else**:

| Level | Means | Knobs |
|---|---|---|
| **Off** | Plans are prose; nothing is signed or enforced | `AIQE_SPEC_MODE=0` |
| **Reviewed plans** *(default for new estates — Q1)* | Plans are structured and signed by a human before generation | spec mode on; `requirements_gate: off`; `enforce: off` |
| **Validated criteria** | Acceptance criteria are formalized and approved before planning | + `requirements_gate: on` |
| **Enforced coverage** | The gate holds generation to **signed** plans' approved scenarios (waivers with expiry). **Prose plans are exempt and the level says so** — an estate full of prose plans under this level enforces nothing, and discovering that from the description beats discovering it from the first exempt plan sailing through | + `enforce: warn` then `strict` |

- **C1.0** — "Enforced coverage" SHALL render its **sub-state visibly**:
  `warn` shows a **dry-run badge** ("reporting, not refusing"), `strict`
  shows enforcing. v1 collapsed two operationally opposite behaviors —
  report vs. refuse — under one level name; the two-step rollout stays two
  steps *and looks like two steps*, and the derivation never silently calls
  them the same level.
- **C1.1** — **One definition module** maps levels ↔ knobs; Settings renders
  the *effective* level derived **through the engine's own resolvers — in
  practice, riding `governance()`, which exists for exactly this**. The
  three knobs span two config stores with different precedence
  (`AIQE_SPEC_MODE` is environment, where an explicitly exported variable
  overrides `.env`; the other two are org-config), and this platform has
  already paid for a view that re-read config instead of asking the
  resolvers: it "reports *off* while the gate refuses." v1's "derived from
  actual knob values" invited that exact bug back through the feature meant
  to prevent it. Hand-set combinations that match no level render as
  **Custom** with the raw resolved knobs — never coerced, never mislabeled
  (C13: the display derives from what is true, not from what was last
  clicked).
- **C1.2** — Applying a level writes only the mapped existing knobs — pinned,
  so a level can never quietly acquire semantics of its own.
- **C1.3** — The governance page and the Start-here panel state the estate's
  current level by name, with the same one-sentence consequence.

---

## 7. Success metrics — honest about what a fixture can measure

| # | Metric | Baseline | Target | Method |
|---|---|---|---|---|
| M1 | SDD terms in view copy with glossary definitions | no glossary | 100%, **pinned** (A2.1) | term-extraction test |
| M2 | SDD refusals carrying the tested message contract | uneven | 100% of the B3 list, one fixture each | `make eval` fixtures |
| M3 | Journey states rendering plain labels + one next action | 0/6 | 6/6, derived from `spec_workflow` output (pin) | view fixture |
| M4 | Wizard shows the requirements step when gated | absent | present-when-on, absent-when-off, both pinned | wizard fixture |
| M5 | Level ↔ knob mapping single-sourced | n/a | one module, drift pinned (C1.2) | pin |
| M6 | Time-to-first-approved-plan for a new user; SDD questions reaching the platform team | unmeasured | **report-only baselines** — comprehension is a human outcome; a fixture asserting "users understand" would be theatre, so the numbers are collected and read, not targeted | onboarding notes + support log |

The split is deliberate: M1–M5 pin the *mechanisms* of clarity; M6 admits
that clarity itself is measured in humans.

---

## 8. Delivery plan

No feature flags. A clarity layer that needs a flag has failed at its one
job, and every change is presentation over unchanged mechanics — the risk
lives in pinned docs, which the docs-currency pins already force into the
same commit.

| Slice | Scope | Exit criteria |
|---|---|---|
| **S1 — Vocabulary** | A1, A2, A3 | term policy applied to UI copy + ui-guide + use-cases; glossary live with the coverage pin; state label pairs rendered |
| **S2 — Journey + refusals** | B1, B3 | one-next-action per state derived from `spec_workflow` (pin); all refusal fixtures green; one message builder |
| **S3 — Adoption levels** | C1 | levels in Settings; effective-level derivation incl. Custom; mapping pin; governance page + Start-here state the level |
| **S4 — Wizard + benefit moments** | B2, B4 | requirements step conditional and pinned both ways; approval confirmation states the payoff |

---

## 9. Risks

| # | Risk | L | I | Mitigation |
|---|---|---|---|---|
| R1 | Label churn breaks pinned docs/tests | H (by design) | L | the pins are the safety net, not the obstacle: ui-guide/nav/docs pins force same-commit updates — schedule them into each slice rather than discovering them in CI |
| R2 | Two vocabularies coexist during rollout (old docs say "spec", UI says "test plan") | M | M | A1.2 scopes the two user-facing docs into S1; ⓘ affordances bridge (plain label ↔ machine term) |
| R3 | Presets tempt future semantics ("Enforced coverage could also enable X") | M | H | C1.2 pin: a level writes only mapped existing knobs; new semantics require a new knob first, then a mapping change — two visible steps |
| R4 | Simplification pressure turns into hiding states | M | H | §3.2 non-goal + M3 requires all six rendered; the C13 framing is in the PRD precisely so "just collapse the early states" has to argue against it in review |
| R5 | The word "spec" cannot be fully evicted (file suffix `.spec.js` is the ecosystem's) | certain | L | the policy renames *our* artifact, not theirs — "test plan" vs. "test files" never collide; the suffix is quoted as a filename, which no one misreads |

---

## 10. Open questions

| # | Question | Owner | Needed by |
|---|---|---|---|
| Q1 | Default adoption level for new estates: **Reviewed plans** (proposed — the loop's value with one ceremony) or Off (maximally unsurprising)? | Product + QE Lead | S3 |
| Q2 | Final nav name for the journey view ("Plan → tests journey" / "Ticket to tests" / keep "Spec workflow" with subtitle) — user-test the three with the pilot team, **using Appendix A's five-sentence model as the comprehension probe**: after one session with each candidate, can the tester retell the loop? This doubles as M6's missing instrument — the same probe, run at baseline and after S1/S2, is the closest a human-comprehension metric gets to a protocol | Product | S1 |
| Q3 | Should EARS notation be visible to requesters at all, or only to QA (requesters see plain criteria, QA sees the EARS form)? | QE Lead | S1 |
| Q4 | Does the governance page adopt the same term policy (it is generated from the constitution, whose clauses use internal names — label layer there too, or keep it as the one deliberately-internal document)? | Product | S3 |

---

## Appendix A — The five-sentence explanation the product should teach

The target mental model, stated so every surface can be checked against it:

> You bring a ticket. The platform drafts a **test plan** — a list of
> scenarios — and a human **approves** it before any tests are written.
> Generation then follows the approved plan, and the delivery gate can check
> that every approved scenario is covered (or explicitly waived). If the
> application changes underneath an approved plan, the plan is **flagged for
> re-approval** rather than silently going stale. Everything else — EARS,
> signatures, waiver files — is machinery in service of those four sentences.

A user who can repeat this after one session is the exit criterion this PRD
actually cares about; M1–M5 exist because that sentence cannot be a fixture.

---

## Appendix B — Revision history

**v2 (2026-08-06)** — after an adversarial gap review of v1, nine findings.
The largest was against this document's own centerpiece, and it is kept here
in full because it *demonstrates the diagnosis*: labels authored from
intuition lie in exactly the way this PRD accuses machine names of confusing.

| Change | Driven by |
|---|---|
| A3 label table corrected and now **derived from the code, per row, with the firing condition quoted**; A3.2 pins each label to its condition | Finding 1 — v1's table authored labels from intuition and got **four of six wrong**, all shifted one stage optimistic: it read each state as the milestone *achieved* where the code fires it for the milestone *pending* (`plan` = no plan yet, `approved` = awaiting approval, `committed` = no gate commit recorded, verified at `spec_workflow.py:182–198`). The v1 label for `committed` — "Tests delivered to the repo" — claimed precisely what had not happened. A friendly label that lies is worse than an opaque machine name |
| D5 evidence strengthened with both citations | Finding 2 — verified: raw `escHtml(r.state)` at `dashboard.py:1965–1970`, no label map anywhere |
| A1.0 + table rows 2–3: the approved-signed / approved-prose distinction | Finding 3 — approval and signing are different events, prose plans are approved-but-exempt, and v1's single "approved test plan" term made every prose-plan estate read signed guarantees into plans exempt from all of them |
| B4 payoff line derives from what the approval actually did; prose confirmation states the exemptions | Finding 4 — v1 would have recited drift-watch/enforcement guarantees at the approval of a plan exempt from both: the friendly lie, delivered at the workflow's most trusted moment |
| C1.1 derivation rides `governance()` / the engine's resolvers; the two-store precedence split named | Finding 5 — the platform already paid for a view that re-read config: it "reports *off* while the gate refuses"; v1's phrasing invited that bug back through the clarity feature |
| A2.1 rebuilt on term markup with a both-directions pin + ambiguous-word lint | Finding 6 — v1's "extract terms and check they're defined" was circular: knowing which words are terms *is* the glossary, so it could never catch a new undefined term |
| C1.0 dry-run badge; "Enforced coverage" never silently equates warn and strict | Finding 7 |
| B3.1 names the builder's home (Python module; bash calls it à la `ticket_fields`; UI fetches) | Finding 8 — an architectural claim with no address is a wish |
| A1.2 scope gains `getting-started.md`'s SDD demo; Q2 gains the Appendix-A comprehension probe, instrumenting M6 | Finding 9 |
