# Spec-driven development — design & architecture

Companion to [spec-driven-stories.md](spec-driven-stories.md): how the SDD
backlog is built. Read `docs/architecture.md` §5.4 (plan-first), §5.8 (phase
chain), §5.13 (retrieval stack) first — SDD **upgrades those rails, it adds no
parallel machinery**. Conventions inherited: stdlib-only, `fs_lock` atomic IO,
mock-first, config layering, per-mechanism kill switches.

## 1. The core inversion

Today: `ticket → analyze → testplan.md (human signs prose) → generate
(interprets prose) → gate (verifies execution, not satisfaction)`.

SDD: one **spec chain** with machine-checkable joints:

```
ticket ──analyze──▶ requirements.yaml (R1..Rn, EARS, ambiguities)
                       │  optional human gate (spec.requirements_gate)
                       ▼
        ──testplan──▶ testplan.yaml (S1..Sn: GWT steps, requirement_refs,
                       │             verification, data contract)   + waivers.yaml
                       │  human gate (existing plan approval — now signs the SPEC)
                       ▼
        ──generate──▶ tests stamped scenario_id, asserting `verification`
                       ▼
        ────gate────▶ existing checks + spec-satisfaction (off|warn|strict)
                       ▼
        ───trace────▶ requirement → scenario → test → gate → CI (+ waivers)
        ───drift────▶ app-contract diff marks scenarios stale (maintain)
```

Everything a human signs is structured; everything structured is rendered to
markdown for the human; every joint is verifiable by a deterministic check.

## 2. The spec store

```
specs/<KEY>/requirements.yaml    # epic 2 (optional stage)
specs/<KEY>/testplan.yaml        # epic 1 (the plan's source of truth)
specs/<KEY>/waivers.yaml         # epic 3 (human escape hatch, expiring)
specs/platform/constitution.yaml # epic 5
```

TRACKED (like `testplans/`), exported in the state bundle's full AND knowledge
profiles (specs are transferable wisdom), removed by clear-demo, rendered —
never hand-duplicated — into `testplans/<KEY>.md` by `spec_render.py`.
Lifecycle state stays in `plan_state` (one store; new fields, not a new file):
`spec_sha`, `requirements_status`, `stale`, `waived`.

### Spec schemas (full-property JSON-schema, unlike the required-only phase contracts)

```yaml
# requirements.yaml
key: PROJ-301
requirements:
  - id: R1
    ears: "WHEN a discount over 90% is submitted, THE SYSTEM SHALL reject it with 422"
    source: "AC-2"                  # which AC/comment it formalizes
    blocking_ambiguity: null        # or the question that must stop the chain
# testplan.yaml
key: PROJ-301
scenarios:
  - id: PROJ-301-S1
    title: boundary rejection >90%
    layer: api
    target_repo: e2e-api-tests-1
    requirement_refs: [R1]
    steps:                          # Gherkin STEPS, not .feature files
      given: "an order of $100 exists"
      when:  "a 91% discount is POSTed"
      then:  "the API responds 422 and the order total is unchanged"
    verification:                   # what a satisfying test MUST assert
      - "response status is 422"
      - "order total unchanged after rejection"
    data: {ref: d1}
# waivers.yaml
waivers:
  - scenario: PROJ-301-S3
    reason: "authz covered by platform-level contract tests"
    by: qa-lead
    expires: 2026-10-01
```

Validation: `engine/lib/spec_store.py` — `load/validate/save` (guarded, atomic,
schema-checked via the same `extract_contract` validator), `render(key)`,
`diff(key)` (scenario-level: added/removed/steps-changed/verification-changed).

## 3. Pipeline integration (per phase, minimal diffs)

| Joint | Change | Fallback (flag off / spec absent) |
|---|---|---|
| analyze | prompt also emits `requirements[]` (EARS); `spec_store` writes requirements.yaml | today's `behaviors` path untouched |
| requirements gate | `pipeline.sh requirements <KEY>` — mirror of plan mode (stop, comment, state) behind `spec.requirements_gate` | not invoked |
| testplan | prompt emits the full scenario schema; contract IS the spec; `spec_store` writes + renders md | free-form plan, exactly today |
| adversary | operates on the spec; arbiter's superset property becomes schema-checked (may only append scenarios) | today |
| approval | `plan_state.set_status` records `spec_sha`; versions snapshot the YAML beside the md | signs md only |
| generate | context gains the spec file; prompt: assert every `verification` clause | today |
| gate | `engine/gate/checks/spec_satisfaction.sh` (new, ordered after born-mapped): resolve scenario_ids → approved spec; coverage-or-waiver; exit 8 in strict | check absent / warn prints |
| trace | requirement_refs + waiver columns | blank cells |
| maintain | `spec_drift.py`: harvested contract diff ∩ scenario endpoints (extend_scout normalizer) → `stale` flags + notify | step no-ops |

Flags: `AIQE_SPEC_MODE` (master, default 1 = structured specs accepted/emitted
when present), org-config `spec.requirements_gate` (off), `spec.enforce`
(off|warn|strict, default off). The gate's strict mode is the only place SDD
can refuse work — everything else degrades to today's behavior, pinned.

## 4. What deliberately does NOT change

- The gate remains the only writer; spec enforcement is a read-only check.
- Approval flows, edit-revokes-approval, adversary-before-human: unchanged —
  they gain precision, not new states.
- No `.feature` files, no BDD runner: each test repo keeps its own framework;
  GWT lives in the spec, `verification` phrases what asserts must exist, and
  the advisory critic (spec-mismatch finding) judges fidelity the gate can't.
- Free-form estates: a plan without a structured spec behaves byte-for-byte as
  today (golden-pinned) — SDD is adoptable per ticket, not a migration cliff.

## 5. Interactions with the shipped stacks

- **Retrieval (§5.13):** scenario chunks become per-scenario (sharper reuse and
  prior-art); the spec file joins the must-keep tier for `tests`-mode runs.
- **Plan reuse:** adaptation re-stamps at scenario granularity; the VERIFY
  checklist is generated from the spec diff instead of boilerplate.
- **Cost:** requirements formalization rides the existing analyze call (one
  prompt, same tier — no new phase spend); the requirements gate, like
  plan-first, SAVES money by stopping bad chains before generation.
- **Telemetry:** spec-satisfaction results land in the run record
  (`spec: {covered, waived, missing, stale}`) and the wizard renders them.

## 6. Testing strategy

`test_spec_store.py` (schema validation, render determinism, scenario-level
diff, guarded IO), `test_spec_gate.py` (satisfaction matrix: covered / waived /
missing / unapproved-id / expired waiver; exit 8 only in strict; PR-path
exempt), `test_requirements_gate.py` (blocking ambiguity stops with a comment;
off = byte-identical flow), `test_spec_drift.py` (contract diff → stale, never
auto-edit), `test_constitution.py` (every clause names a real pin), plus golden
pins that spec-less estates are untouched. Adversarial UAT pass (Pass-9 style)
before `strict` is documented as safe: forged scenario_ids, waiver abuse
(expired/missing reason), spec text framed as instructions, tampered spec_sha.

## 7. Build order

| Slice | Stories | Ships |
|---|---|---|
| 1 | 1.1, 1.2, 1.3 | spec store + schema + renderer + spec-signed approval |
| 2 | 2.1, 5.1 | EARS requirements + machine constitution |
| 3 | 2.2, 2.3 | requirements gate + blocking clarifications |
| 4 | 3.1, 3.3, 6.1 | spec-consuming generation + trace closure + spec UI |
| 5 | 3.2 (warn) | satisfaction check in warn, adversarial gate tests |
| 6 | 4.1, 4.2 | drift + re-verification |
| 7 | 6.2, 6.3, 5.2 | exports, reuse sharpening, executable eval specs |
| 8 | 3.2 (strict) + UAT | strict mode go/no-go after a warn-mode sprint |

Each slice demo-green behind flags; `strict` is a two-step rollout by design.
