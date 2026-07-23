# OpenHands capability review — what we should adopt

A review of current OpenHands (V1 SDK / Agent Server / Cloud / Enterprise) against this
platform, aimed at maturing test-plan generation, E2E generation for UI **and** API
suites, coverage-gap detection, and new-test recommendation.

**How to read this:** every item says what OpenHands offers, what we do today, and
whether adopting it is worth it. Items are ordered by value-to-effort. Where a
capability could not be verified from primary docs, it is called out rather than
assumed. Setup steps live in [openhands.md](openhands.md); this file is the *decision*
record for what we adopt **from** OpenHands. Its inverse — what happens when you have
no OpenHands at all — is [standalone-operation.md](standalone-operation.md).

> Verification note: OpenHands moved fast between V0 and V1 — agent classes collapsed,
> microagents were renamed, and the OSS resolver moved behind the enterprise licence.
> Claims below were checked against `docs.openhands.dev` and the public repos. Anything
> we could not confirm is marked **⚠ unverified**.

---

## 1. Act on these now (correctness / deprecation)

### 1.1 Cloud API V0 → V1  *(done in this repo)*
OpenHands Cloud's `POST /api/conversations` (V0) was scheduled for removal in 2026;
Cloud now uses **`POST /api/v1/app-conversations`** with a different body
(`initial_message.content[]`, `selected_repository`). Self-hosted Agent Server still
uses `/api/conversations`.

*We already parameterise this* (`OPENHANDS_CONVERSATIONS_PATH`), so no code was broken —
`bin/smoke-openhands.sh` and this doc now state the Cloud path and body shape explicitly.

### 1.2 Microagents → Skills  *(done in this repo)*
`.openhands/microagents/` is deprecated. Resolution order is now
`.agents/skills/` → `.openhands/skills/` → `.openhands/microagents/`, first match wins,
with per-skill directories (`SKILL.md` + optional `scripts/`, `references/`) and
progressive disclosure.

We ship `triggers/openhands/skills/ai-qe/SKILL.md` as the current form and keep the
legacy microagent, marked deprecated, for older deployments.

---

## 2. High value — recommended next

### 2.1 Webhooks instead of polling  ★ highest value — **implemented**
OpenHands' `WebhookSpec` POSTs buffered agent events to a URL you own, with custom
headers (bearer auth), buffer size, flush delay and retry policy. There is also
`GET /api/conversations/{id}/agent_final_response`.

**Today** we start a conversation and then rely on the pipeline reporting for itself; a
long-running OpenHands run is otherwise opaque to us. **We already own the right
receiver** — `bin/taskevent_receiver.py` (validated, idempotent, `/healthz`, queue
enqueue). Pointing `WebhookSpec.base_url` at it, with a new event shape alongside
TaskEvent, gives live progress and a definitive completion signal for free.

**Effort:** small. Mostly a new schema + handler branch in the existing receiver.

**Status: done.** The receiver now accepts `POST /hooks/openhands/events` and
`/hooks/openhands/conversations` (point `WebhookSpec.base_url` at
`<receiver>/hooks/openhands`; auth via `Authorization: Bearer` since that is the only
header form WebhookSpec can express). Records land in `reports/openhands/state.json`
via `engine/lib/openhands_events.py` — bounded, tolerant of schema drift between
versions, and **observability only**: these routes never enqueue work, so agent
chatter cannot start pipeline runs. Surfaced by `bin/qa.py openhands`,
`GET /api/openhands`, and an *OpenHands agent runs* card in the Runs view. Setup:
[openhands.md](openhands.md) Step 4b.

### 2.2 Stop hooks as a pre-completion gate  ★ strong architectural fit — **implemented**
`.openhands/hooks.json` supports `pre_tool_use`, `post_tool_use`, `user_prompt_submit`,
**`stop`**, `session_start`, `session_end`. A `stop` hook can **block task completion**
(exit 2, or `{"decision":"deny","reason":…}`) until repo checks pass.

This maps almost exactly onto our deterministic gate. Wiring `engine/gate/gate.sh` as a
Stop hook means an OpenHands-driven run cannot declare success while the gate would
reject it — enforcing our central invariant *inside* the agent loop instead of only
after it. Our gate already returns distinct exit codes, so the reason string is easy.

**Caveat:** the gate must stay the only push path; the hook runs it, it does not
replace it.

**Status: done.** `.openhands/hooks.json` binds the blocking `stop` event to
`.openhands/hooks/gate-check.sh`. The hook runs the gate in a new **check-only mode**
(`AIQE_GATE_CHECK_ONLY=1`) which performs every check — scope + filename charset,
born-mapped sidecar, lint, executing the changed specs, secret scan — and stops
before writing, reporting `GATE_STATUS=WOULD_COMMIT`. On failure it returns
`{"decision":"deny"}` and exit 2, naming the rule and the offending file so the agent
can fix and retry.

Two properties are enforced by test rather than convention: the hook **never commits
or pushes** (the gate remains the only writer, and the default gate path still commits
as before), and it **fails open** — if it cannot determine an answer it allows
completion rather than blocking on its own malfunction, since the real gate still runs
afterwards and will reject.

### 2.3 Adopt the `qa-guide.md` skill convention for test repos
OpenHands' public `qa-changes` skill is customised per repo via
`.agents/skills/qa-guide.md`. Our guidance-sync feature already pulls repo-owned
`AGENTS.md`/`CLAUDE.md` via the Scm port's `fetch_file`.

Adding `.agents/skills/qa-guide.md` (and `SKILL.md`) to the filenames
`engine/lib/guidance_sync.py` fetches would let teams that already use OpenHands keep
one guidance file that both systems honour. **Effort: trivial** — extend
`GUIDANCE_FILES`.

### 2.4 Path-triggered skills for UI vs API conventions — **implemented**
Skills can trigger on path globs (`src/api/**/*.ts`, `**/*.route.ts`), injected when the
agent touches a matching file. That is a cleaner mechanism than our single estate-wide
`AGENTS.md` for the UI-vs-API split we currently express through repo `layer` and
`skills/e2e-{api,ui}-conventions/`.

**⚠ Caveat:** path triggers do **not** fire in ACP-backed conversations (see 3.1), and
they are recent additions — validate before depending on them.

**Status: done.** `bin/gen_path_skills.py` emits
`.agents/skills/e2e-{api,ui}-conventions/SKILL.md` with `paths:` frontmatter. The
globs are **derived from the registry** — each test repo already declares its `layer`
and `layout`, so adding a repo or changing a layout keeps the triggers correct with
no hand-edited globs (regenerated by `make skills`/`make agents` and by every
`repo_admin` mutation). The convention text stays single-sourced in
`skills/e2e-<layer>-conventions/`; the generator only wraps it with triggers.

Because the caveat above is real, this is an **enhancement, not a replacement**:
`AGENTS.md` remains always-on, so in an ACP conversation (where path rules don't
fire) the conventions still reach the agent — just without the split.

One trap worth recording: a bare repo-relative `tests/**` (the UI specs dir) also
matches this control repo's own `tests/gate-adversarial.sh`, which would inject UI
page-object rules whenever an agent touched the gate harness. Repo-relative spec
globs are therefore scoped to `*.spec.{js,ts}` — truer to the signal anyway — and a
test pins it.

---

## 3. Worth evaluating (bigger changes)

### 3.1 ACP — run Claude Code as a first-class OpenHands agent
`ACPAgent(acp_command=["npx","-y","@agentclientprotocol/claude-agent-acp"])` runs Claude
Code natively inside OpenHands, which would collapse our two-layer design (OpenHands
orchestrates → Claude Code works in the sandbox).

**Do not rush this.** On `ACPAgent`, `tools`, `mcp_config`, `condenser` and `critic`
raise `NotImplementedError`, and path-triggered skills don't fire. Our phase chain
depends on per-phase `--allowedTools` and `--max-turns`, which we would lose. Revisit
when ACP reaches parity.

### 3.2 Critic model as a second opinion before the gate — **implemented (advisory only)**
`APIBasedCritic` scores a proposed completion 0.0–1.0 and, with
`IterativeRefinementConfig`, auto-retries below a threshold. This is close to our
validate→repair loop, but model-based rather than execution-based.

Our gate is deliberately **not** an LLM, and that should not change. A critic is
worth trialling as an *advisory* signal recorded in the run record (e.g. alongside
`repair_loops`) — never as a substitute for lint/execute/secret-scan.

**Status: done — the score, not the retry.** A `critic` phase runs after `validate`
and scores the generated specs with categorized findings (`vacuous`, `weak`,
`duplicate`, `missing`, `brittle`, `unclear`). We deliberately did **not** adopt
`IterativeRefinementConfig`: auto-retrying below a threshold puts a model in the
approval path, which is the one thing our gate design rules out.

It earns its place by covering what the gate structurally cannot. The gate proves a
spec lints, runs, passes, holds no secrets and is catalog-mapped — it cannot tell a
real assertion from `expect(true)`, or spot the fourth spec re-testing what three
others cover. That is the **escaped noise** metric the scorecard has defined since
v2.0 and never had a source for; `make critic` and `python3 eval/scorecard.py` now
report it.

"Advisory" is enforced structurally, each property pinned by a test in
`registry/tests/test_critic.py` rather than left to convention:

| Property | How |
|---|---|
| Cannot change a commit decision | `overall` comes from gate results alone; a test asserts nothing under `engine/gate/` so much as mentions the critic, and that a 0.0 score still yields `committed` |
| Cannot repair what it grades | phase `allowed_tools: "Read"` — no Write/Edit/Bash |
| Cannot fail a run | the phase runs non-fatally; `engine/lib/critic.py` is total (bad JSON, crashed phase, unreadable config → "no signal") |
| Cannot move a review status | `review_state.set_critic()` never touches `status`, so a low score cannot un-approve reviewed work |

Verdict labels are recomputed from org-config thresholds rather than trusted from the
model, and `noise_count` is clamped to the specs actually reviewed. Surfaced on the
run summary posted to the PR/ticket, on the key's review entry, in the Runs view
beside the gate outcome, via `bin/qa.py critic --findings`, and in the scorecard.
`AIQE_CRITIC=0` skips it for one run; `critic.enabled: false` disables it estate-wide.

### 3.3 Sub-agents for parallel per-repo generation
File-based sub-agents (`.agents/agents/*.md`) with their own `tools`, `model`, `skills`,
`mcp_config` and budget, plus `TaskToolSet`/`DelegateTool`. Note `enable_sub_agents` is
**False by default**, and `DelegateTool` has no official docs page (**⚠ unverified**).

Potential fit: fan out generation across the test repos a run resolves to. Our gates
already run in parallel; generation does not. Only worth it if cross-repo runs become a
bottleneck.

### 3.4 Managed MCP / LLM gateway (Enterprise)
Enterprise adds a LiteLLM gateway with budgeting, plus "managed MCP hosting"
(**⚠ underspecified — bullet points, no dedicated doc page**). Our `MAX_COST_USD_PER_RUN`
and `MAX_WALLCLOCK_MIN` settings are currently **not enforced anywhere** (the Settings
UI labels them as orchestrator-enforced). Routing runs through an enterprise LLM gateway
would make those budgets real rather than advisory.

---

## 4. Overlap to be deliberate about

OpenHands now ships a **QA agent** (`qa-changes` skill): Understand → Setup → Exercise →
Report, posting PASS/FAIL/PARTIAL to the PR. It explicitly *does not* run test suites —
it exercises the software manually — and it does not maintain a durable test estate.

That is complementary, not competing: we generate and **commit** maintained E2E tests
into real test repositories, born-mapped to a catalog, with coverage-gap analysis. The
sensible split is to let their QA agent do exploratory verification of a change while we
own the regression suite. Worth stating explicitly so we don't rebuild their feature or
vice versa.

Two of their design choices validate ours: `/api/git` is **read-only** (agents cannot
push behind our back — consistent with "the gate is the only push path"), and their
Verification Stack blog argues for exactly the layered gating we implement.

---

## 5. Explicitly not adopting

| Capability | Why not |
|---|---|
| OSS resolver / GitHub Action | The OSS resolver package is gone (docs link 404s); resolver logic now lives under the PolyForm-licensed `enterprise/`. Our Path 2/3 triggers (GH Actions, Bitbucket Pipelines, Jenkins) already cover this without a licence dependency. |
| OpenHands as an MCP **server** | It is an MCP client only; no server is exposed. Nothing to integrate. |
| Air-gapped enterprise install | Not documented, and the Slack integration docs state it is unsupported. |
| Audit logging | **⚠ marketing claim only** — no documentation found. Do not promise it to stakeholders. Laminar tracing is the only documented observability. |

---

## 6. Suggested sequence

1. **Webhooks → `taskevent_receiver`** (2.1) — biggest observability win, small change.
2. **`qa-guide.md` in guidance sync** (2.3) — one-line change, immediate interop.
3. **Stop hook running the gate** (2.2) — enforces our core invariant inside the agent loop.
4. Then evaluate path-triggered skills (2.4) and the critic-as-advisory-signal (3.2).
5. Defer ACP (3.1) until tool/MCP parity lands.

**Where this landed:** 1–4 are implemented (2.1 webhooks, 2.2 Stop hook, 2.3
`qa-guide.md` in `GUIDANCE_FILES`, 2.4 path-triggered skills, 3.2 advisory critic).
3.1 (ACP) stays deferred on a real upstream blocker — `tools`, `mcp_config`,
`condenser` and `critic` raise `NotImplementedError` on `ACPAgent`, so adopting it
would cost us per-phase `--allowedTools` and `--max-turns`. 3.3 (sub-agents) and 3.4
(enterprise gateway) remain unjustified at current scale.

Licence reality check: everything under `enterprise/` is **PolyForm Free Trial** (30
days/year without a commercial licence). The SDK, agent server, `extensions` skills and
benchmarks are MIT. Items 1–4 above rely only on MIT-licensed surfaces.
