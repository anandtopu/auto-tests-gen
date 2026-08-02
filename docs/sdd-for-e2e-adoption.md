# Spec-driven development for E2E tests — adoption analysis

**The ask.** Use spec-driven development to build E2E tests: cut the cost of
building them, and establish a standard workflow, process, guidelines and
governance that draw on the artifacts and repositories the estate already has —
with usable guidance available *from the user interface*.

**The short answer.** Most of the machinery exists and is good. It is also
**off by default, invisible in the UI, and disconnected from the cost levers**.
This is an adoption problem far more than a build problem, and saying so is
more useful than designing a second system next to the first.

---

## Part 1 — What is already built (verified in source)

| capability | where | state |
|---|---|---|
| Structured spec of record | `spec_store.py` → `specs/<KEY>/testplan.yaml` | built |
| Plan rendered FROM the spec | one source of truth, header says so | built |
| EARS requirements + ambiguities | `make requirements`, `requirements.yaml` | built |
| Requirements approval signs a sha | `make requirements-approve` | built |
| Blocking-ambiguity stop | exit 65 `NEEDS_CLARIFICATION` + ticket comment | built |
| Human plan approval gate | `plan_state`, approval signs `spec_sha` | built |
| Gate-level spec satisfaction | `engine/gate/spec_check.py`, exit 8 | built, **off** |
| Waivers with expiry | `specs/<KEY>/waivers.yaml` | built |
| Spec drift detection | `spec_drift.py` on `make maintain` | built |
| Re-verify cataloged tests | `make spec-verify KEY=..` | built |
| Machine-readable constitution | `specs/platform/constitution.yaml` | built |
| Traceability matrix | ticket → scenario → spec → commit → CI | built |
| Semantic plan reuse | `plan_reuse.py` | built, **off** |

That is a genuinely complete SDD spine. The problem is not missing parts.

## Part 2 — The three real gaps

**G1 — Governance ships off, and nothing tells anyone.**
`spec.requirements_gate: off`, `spec.enforce: off`, `AIQE_PLAN_REUSE` defaults
to 0. Each default was correct as a rollout decision — a two-step warn-then-
strict rollout is right, and reuse was gated on an eval that has not run. But
the combined effect is that a team can use this platform for months and never
touch spec-driven development, without ever being told the capability exists or
what turning it on would buy them. **An off-by-default feature with no
discoverability is indistinguishable from an unbuilt one.**

**G2 — The spec workflow has no UI at all.**
`grep` over `bin/dashboard_server.py` finds **zero** `/api/requirements` or
`/api/spec*` endpoints. The dashboard mentions waivers twice and EARS, the
constitution and requirements approval not at all. Every step — author
requirements, review ambiguities, approve, waive, check drift, re-verify — is
CLI-only, and the guidance for all of it lives in two markdown files
(374 lines) that a dashboard user never opens. The ask says "clear usable
guidelines from the user interface"; today there is no interface.

**G3 — The cost levers are not wired to the spec.**
The platform has real cost machinery — phase cache, scoped context, plan reuse,
degradation ladder, per-workflow envelopes. None of it is *driven by the spec*.
A signed spec is the strongest reuse signal the estate has (it is human-approved,
structured, and scenario-addressable), and today it is not consulted when
deciding whether an LLM call is needed at all.

---

## Part 3 — How SDD actually reduces E2E test cost

Not "AI writes tests faster". The savings are structural, and each is
measurable against the existing cost report.

**1. The expensive failure is a wrong test, not a slow one.** An E2E test that
encodes a misunderstanding costs a review cycle, a debugging session, and often
a flake quarantine months later. Requirements approval moves the disagreement to
a paragraph of EARS text — the cheapest artifact in the chain to change.

**2. A scenario is a cache key.** Structured scenarios are addressable, so an
unchanged scenario needs no authoring call at all. The phase cache already keys
on content; the spec makes "unchanged" a *scenario-level* fact instead of a
whole-plan one.

**3. Approved specs are the reuse corpus.** `plan_reuse` already prefers
human-approved plans. Signed specs make that corpus trustworthy enough to raise
the threshold — reuse becomes deterministic text surgery instead of a model call.

**4. Coverage becomes subtraction.** With `covers:` and the trace matrix, an
approved scenario already exercised by an existing test is a scenario nobody
should pay to author again. This is the single largest saving on a mature
estate, and it needs no new LLM capability — only the join the trace matrix
already computes.

**5. Drift replaces re-authoring.** `spec_drift` flags scenarios whose surface
vanished. Repairing a flagged scenario is cheaper than regenerating a plan, and
targets the work at what actually changed.

**Honest caveat.** None of these is measured yet on this estate. The cost report
can price them once real runs exist, and `make parity-*` — the harness that
would produce those runs — is still blocked on CLI auth. Any number I quoted
here would be invented, so I have quoted none.

---

## Part 4 — The workflow, as it should be presented

Six states, each with one owner and one exit condition. This is what the UI
should teach, because a process nobody can see is a process nobody follows.

```
TICKET ──▶ REQUIREMENTS ──▶ PLAN ──▶ APPROVED ──▶ TESTS ──▶ COMMITTED
            (EARS,           (spec    (signed      (generate  (gate,
             ambiguities)     of       spec_sha)    from the   born-mapped)
                              record)               spec)
```

| state | owner | exit condition | governance |
|---|---|---|---|
| Requirements | BA / QE lead | every blocking ambiguity answered | `requirements_gate` refuses planning until approved |
| Plan | QE author (+ adversary) | scenarios have given/when/then | adversary may only ADD scenarios |
| Approved | reviewer | approval signs the spec sha | editing an approved plan revokes approval |
| Tests | platform | generated FROM the signed spec | `spec.enforce` refuses uncovered scenarios |
| Committed | the gate | born-mapped, lint+tests pass | the gate is the only push path |
| Live | QE lead | drift resolved, waivers unexpired | drift notifies; waivers expire by date |

**Governance principles worth stating explicitly**, because each already holds
in code and none is visible to a user:

1. A spec is signed, not merely saved — approval binds to a content hash.
2. A human's free-form edit supersedes the structured spec, preserved for
   forensics. Prose a person wrote wins.
3. An uncovered approved scenario is either covered, waived with a reason and an
   expiry, or the gate refuses. Waivers expire so "temporarily" cannot mean
   "forever".
4. Strict enforcement is a two-step rollout: `warn` until the signal is clean,
   then `strict`.
5. The adversary is read-only. An opponent that can edit the plan is a second
   author.

---

## Part 5 — What to build (proposed backlog)

Ordered by value per unit of work. Each slice ships something usable.

**S1 — Make the workflow visible (highest value, no new engine work).**
A "Spec workflow" view showing the six states for each key, what is blocking,
and the *next action as a button*. Inline guidance at each step, so the rules in
Part 4 are read where the decision is made, not in a markdown file.

**S2 — Requirements + ambiguities in the UI.** `GET/POST /api/requirements` —
review EARS statements, answer blocking ambiguities, approve (signing the sha).
This is the step most likely to be skipped precisely because it is CLI-only, and
it is the step that prevents the expensive failure.

**S3 — Governance settings with consequences shown.** Surface
`requirements_gate` and `spec.enforce` in Settings with a plain-language
explanation of what each does and what will start failing — and default new
estates to `warn`, not `off`. A rollout knob nobody can find is not a rollout.

**S4 — Waivers as a first-class UI object.** Create with a reason, an owner and
an expiry; show expiring ones on the Overview. A waiver file nobody sees becomes
permanent by accident.

**S5 — Spec-driven cost reduction.** Scenario-level cache keys, coverage
subtraction before authoring, and raising the reuse threshold for signed specs.
Ship *after* `parity-*` can measure it — otherwise the savings claim is
invented, which this codebase's cost rules forbid.

**S6 — One governance document, generated.** A single "how we build E2E tests
here" page rendered from the constitution and org-config rather than written by
hand, so it cannot drift from what the code enforces.

---

## Recommendation

Do **S1–S4 first**. They are UI and documentation work over an engine that is
already built and tested, they directly answer "clear usable guidelines from the
user interface", and they convert an unused capability into an adopted one.

Do **S5 after** the parity harness can measure it. The cost story is real, but
this codebase has a standing rule that unmeasured figures are never presented as
measured, and a savings claim is exactly the kind of number people repeat.
