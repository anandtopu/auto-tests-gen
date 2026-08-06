# Product roadmap — enhancements on the current feature set

A product-manager review of what exists, what it should become, and in what order.
Grounded in this deployment's own telemetry rather than aspiration: the scorecard
currently admits `test health: n/a` (no CI results ever ingested), `acceptance rate:
n/a` (the review board has never recorded an approval), and `update-vs-create: 0%`
(a mock artifact — real extend-behaviour is unmeasured). Several roadmap items below
exist precisely to make those numbers real.

Personas: **DEV** (developer whose PR triggers tests), **QA** (QA engineer authoring
and reviewing), **LEAD** (QA team lead owning coverage and quality), **EM**
(engineering manager owning throughput and risk).

---

## 0. What exists today (the base being built on)

- **PR → E2E sync** (Workflow A): rules-first routing, diff triage, per-repo
  generation fan-out with existing-approach exemplars and the test catalog in
  context, deterministic gate (lint → execute → secret scan → born-mapped →
  commit/push), PR build status + coverage-delta comment.
- **JIRA → plan → tests** (Workflow B): issue-type guidance, adversarial plan
  review (author → adversary → arbiter), human approval gate, testdata, generation,
  ticket linking (attachment + summary comment).
- **Test catalog**: bootstrap (extract → correlate → classify → tier), born-mapped
  sidecars, coverage matrix, gap analysis, CI-health ingest, SQLite query index.
- **Operations**: 15-view dashboard with a guided wizard, work queue with
  actionable failure reasons, PR-URL intake, review board with releases, team
  report, email digests, exports (md/html/docx/pdf), Confluence publish.
- **Platform**: OpenHands agents/skills with full request tracing, phase cache,
  model tiers, budget guard, torn-write-safe state stores, portable state bundle.

---

## 0a. Already shipped since this roadmap was written

The tables below are the *original* proposal and are kept as written — they record
what was wanted and why. Fourteen of those items have since been built, so read the
tables with this list in hand: proposing work that already exists is the expensive
direction for a roadmap to be stale in.

| # | Shipped as | Where it lives |
|---|---|---|
| 1.1 | CI results auto-ingest | `POST /hooks/ci/results` (token-gated, 5 MB cap), `make ingest-results` |
| 1.2 | Flake quarantine workflow | `bin/qa.py flaky` / `quarantine` / `unquarantine` over `catalog/health.json` |
| 1.5 | Reviewer assignment & review debt | `review.reviewers` rota in `engine/lib/review_state.py`; review-debt card on Overview |
| 2.1 | Extend-vs-create scout | `engine/lib/extend_scout.py` |
| 3.1 | Traceability matrix | `make trace-matrix`, `GET /api/trace-matrix`, Trace view |
| 3.2 | Risk-weighted gap ranking | `engine/lib/coverage_gaps.py` |
| 3.4 | Coverage drift alarm | `engine/lib/coverage_drift.py`, run by `make maintain` |
| 4.1 | In-UI diff review | Artifacts view (rendered code + before/after) |
| 4.2 | Plan versioning + diff-since-approval | `engine/lib/plan_state.py`, plan editor |
| 4.3 | Batch review | Runs & reviews view — filter a release, approve the remaining set in one confirmed pass |
| 4.5 | Adversary verdicts in the plan reviewer | plan editor cards |
| 5.4 | Scheduled estate maintenance | `make maintain` via `engine/lib/maintenance.py`; `deploy/openshift/cronjob.yaml` |
| 6.1 | Similar prior plans | `engine/lib/plan_similarity.py` |
| 6.4 | Portable state bundle | `make state-export` / `state-import`, `engine/lib/state_bundle.py` |

**1.4 (real cost dashboard) is half-shipped and the half that is missing is the
important one.** The Cost view, the by-phase/by-provider rollups and the regression
alarm all exist; what does not exist is *measured* data, because `make parity-*`
remains blocked on Claude CLI auth. Every figure this estate can currently show is
simulated, and is labelled as such.

Four capability areas shipped that this roadmap never proposed, and they have their
own documents: the [LLM Runner port](multi-llm-providers.md) (provider
independence), the [spec-driven workflow](spec-driven-architecture.md),
[observability](observability-epic.md) (transaction log + alert rules), and the
operator-facing layer — run progress, explainability, bounded retry and selective
approval (architecture §5.20, use cases 16–18).

---

## 1. Close the measurement loop (prerequisite for everything else)

The platform generates tests but cannot yet see how they live or die. Every quality
claim downstream depends on this theme.

| # | Feature | What it is | Builds on | Personas | Effort |
|---|---|---|---|---|---|
| 1.1 | **CI results auto-ingest** | A webhook/poller that feeds JUnit results from Jenkins/GitHub Actions into `catalog/health.json` on every CI run, instead of the manual `make ingest-results` nobody runs. Turns `test health: n/a` into a live number. | `test_health.py`, TaskEvent receiver | LEAD, EM | S |
| 1.2 | **Flake quarantine workflow** | `FLAKY_BAND` already computes flakiness but nothing acts on it. Add: a Flaky view listing sometimes-passing tests, one-click "quarantine" (tag + exclude from gating) and "propose repair" (queue a validate-style repair run scoped to the flaky spec). | 1.1, health ingest, work queue | QA, LEAD | M |
| 1.3 | **Generated-test survival tracking** | For every test the gate commits, track its lifetime: does it still exist in the repo N weeks later, was it modified by humans, deleted, or still passing? "Survival rate" is the truest measure of generation quality — better than any critic score. | run records + guidance sync's `fetch_file` | EM, LEAD | M |
| 1.4 | **Real cost dashboard** | Once `parity-*` is unblocked (Claude CLI auth — REVIEW.md item 5), surface per-run/per-phase/per-repo real spend, cache-hit savings and model-tier mix in the Overview. The plumbing (budget ledger, cache stats) exists; only the display and the real data are missing. | budget.py, phase_cache | EM | S |
| 1.5 | **Acceptance-rate nudges** | The review board has never recorded a decision because nothing pushes reviewers to it. Add reviewer assignment on commit (round-robin or CODEOWNERS-style per test repo), SLA aging on the digest email, and a "review debt" tile on Overview. | review_state, email digests | LEAD, EM | S |

## 2. Make generated tests better (quality of the core artifact)

| # | Feature | What it is | Builds on | Personas | Effort |
|---|---|---|---|---|---|
| 2.1 | **Extend-vs-create scout** | A small read-only phase before generation that decides, per behavior, whether to extend a named existing spec or create a new one — emitting explicit `extend: <file>` targets into the generate context. The catalog now reaches generation; this makes the decision explicit instead of implicit. Directly moves `update-vs-create` off 0%. | catalog slice in context, fan-out | QA, DEV | M |
| 2.2 | **Assertion-strength gate check (advisory→optional-blocking)** | The critic flags vacuous assertions but can never block. Add an *opt-in per-test-repo* gate check (`.ai-qe/config.yaml: assertion_lint`) using deterministic AST rules (no LLM): flag `expect(true)`, status-only assertions on mutating endpoints, awaits without assertions. Deterministic, so it may gate. | gate.sh, per-repo config | QA, LEAD | M |
| 2.3 | **Selector resilience for UI repos** | UI specs break on selector drift. Harvest each UI repo's route table + data-testid inventory into the conventions file, and teach the critic a `brittle-selector` finding (xpath/nth-child/text-match selectors). Optionally propose a `data-testid` PR to the app repo as a suggestion artifact — never auto-pushed. | spec_exemplars, critic | DEV, QA | M |
| 2.4 | **Test-data factory library per repo** | testdata generation currently emits per-ticket JSON. Promote recurring shapes into a curated, versioned factory library per test repo (`.ai-qe/factories/`), which generation is told to reuse — the same exemplar mechanism that already works for helpers. | testdata phase, exemplars | QA | M |
| 2.5 | **Repair-with-context on gate failure** | When the gate's execute check fails, the run quarantines. Add a bounded one-shot repair: feed validate the failing spec + the exact runner output + conventions, retry once, then quarantine. The `escalate` model tier already exists for this and is never used. | validate phase, escalate tier | DEV, QA | M |

## 3. Coverage intelligence (from "gaps list" to "risk map")

| # | Feature | What it is | Builds on | Personas | Effort |
|---|---|---|---|---|---|
| 3.1 | **Requirement traceability matrix** | One queryable chain: JIRA key → plan scenario → generated spec → gate commit → CI health. Most of the links already exist in run records and plan state; join them into a Traceability view + CSV/Xray export. This is the audit artifact regulated teams ask for first. | trace.py, catalog, plan state | LEAD, EM | M |
| 3.2 | **Risk-weighted gap ranking** | `make gaps` lists uncovered surface flat. Weight it: mutating endpoints > read-only, authz-adjacent routes > public, recently-changed (git churn) > stable, then order the gaps view and the generation nudges by that score. | coverage_gaps, harvested facts | LEAD | S |
| 3.3 | **PR coverage policy (required check)** | The `ai-qe` build status is advisory. Add a per-app-repo policy knob: `coverage_policy: off | warn | require` — `require` fails the status when a PR touches surface with no test evidence and no generated test. Teams opt in per repo; the gate stays the only writer. | pr_comment, set_status | EM, DEV | M |
| 3.4 | **Coverage drift alarms** | Nightly scheduled scan (receiver already runs as a service): diff harvested surface vs catalog; when a repo's uncovered-surface count grows week-over-week, notify that repo's channel. Turns the coverage matrix from a report into an alert. | coverage_gaps, notify port | LEAD, EM | S |
| 3.5 | **"Do we already test this?" semantic search** | Natural-language search over the catalog ("charging a stored card with an expired token") answering with matching specs + confidence, backed by embeddings over titles/evidence — with the SQLite index as the exact-match fallback. Kills duplicate authoring at the source. | catalog.db | DEV, QA | M/L |

## 4. Reviewer experience (the human gate is the product's promise — make it fast)

| # | Feature | What it is | Builds on | Personas | Effort |
|---|---|---|---|---|---|
| 4.1 | **In-UI diff review with inline comments** | Today reviewing means reading an archived diff. Render the gate diff side-by-side in Runs/Artifacts with per-hunk comments that land on the review-board note, and Approve/Request-changes at the same surface. One screen from generated code to decision. | artifacts view, review_state | QA, LEAD | M |
| 4.2 | **Plan versioning + diff** | Plans are edited and re-approved but only the latest text survives. Keep per-edit snapshots (plan state already has history entries — attach content), show "what changed since I approved," and make re-approval show exactly the delta. | plan_state history | QA, LEAD | S/M |
| 4.3 | **Batch review** | A release's worth of keys reviewed in one pass: filter by release, walk diffs with keyboard next/prev, approve-all-remaining with one confirmation. The release field already exists on the board. | review board, releases | QA LEAD | S |
| 4.4 | **Slack interactive approvals** | The notify port posts summaries; add action buttons (Approve / Request changes / Open) so a LEAD can clear review debt from Slack. Decisions still land in review_state with the actor recorded. | notify port, review API | LEAD | M |
| 4.5 | **Adversary verdict surfacing per scenario** | The adversarial review currently surfaces one summary line. Show the accepted/rejected gap list itself (title, category, severity, rationale) in the plan reviewer, so the human sees *what* was challenged, not just that a challenge happened. | plan_adversary signal | QA | S |

## 5. Team and scale (from single-operator PoC to team tool)

| # | Feature | What it is | Builds on | Personas | Effort |
|---|---|---|---|---|---|
| 5.1 | **Roles on top of SSO** | The SSO header exists; add role mapping (viewer / reviewer / operator / admin): reviewers can approve, operators can queue runs and edit settings, viewers read. Approval records already capture the actor — enforcement is the missing half. | SSO_HEADER, review/plan APIs | EM | M |
| 5.2 | **Multi-worker queue** | One run at a time per checkout is the current concurrency ceiling. Support N worker checkouts (the pipeline lock is already per-checkout) with the queue assigning work — horizontal scale without touching pipeline semantics. | work_queue, pipeline lock | EM | L |
| 5.3 | **Per-team notification routing** | Route run/review/coverage notifications by test repo → team channel mapping in the registry, instead of one global channel. | registry, notify port | LEAD | S |
| 5.4 | **Scheduled estate maintenance** | One nightly job: guidance sync for all repos, catalog re-bootstrap drift check, health prune, cache prune, state-bundle snapshot to a retention dir. Every piece exists as a make target; the scheduler and the single summary email are missing. | existing targets, receiver | LEAD, EM | S |
| 5.5 | **Onboarding wizard** | The Repositories view can add repos, but a new team's first hour is undocumented clicking. A guided first-run: add app repo → add test repo → scope → bootstrap → first PR dry-run, with progress persisted. The Guided-run wizard pattern already exists to copy. | wizard pattern, onboard.sh | EM, LEAD | M |

## 6. Knowledge reuse (the moat: contextual memory that compounds)

| # | Feature | What it is | Builds on | Personas | Effort |
|---|---|---|---|---|---|
| 6.1 | **Similar-plan retrieval with human diff** | When authoring a plan for a ticket similar to a past one, present the prior plan as a *reviewed starting point* — never silently reused: the reviewer sees "based on PROJ-301 (approved), here's what differs." Cheapest real reduction in plan authoring cost after the phase cache. | plans corpus, phase cache | QA | M |
| 6.2 | **Failure-pattern memory** | When validate repairs a spec or the gate rejects one, record the pattern (error signature → fix applied). Feed the top recurring patterns into the conventions file so generation stops making the estate's favorite mistake. | validate, run records | QA | M |
| 6.3 | **Curated guidance suggestions** | The platform observes what reviewers change in generated tests (1.3's survival diffs). Periodically propose additions to `knowledge/curated/<repo>/` — "reviewers keep renaming X to Y; add a convention?" — as drafts a human accepts. | curated_guidance, 1.3 | QA LEAD | M |
| 6.4 | **Cross-deployment knowledge sync** | The state bundle moves everything; add a *knowledge-only* bundle profile (curated guidance + catalog + conventions, no run history) so a new team bootstraps from an experienced team's knowledge without inheriting their history. | state_bundle | EM | S |

## 7. Deliberately not proposed

- **Auto-approval of plans or reviews above a critic score** — the entire safety
  model is that humans approve and the deterministic gate commits. A score-based
  bypass converts the advisory critic into a gate, which is the one line this
  architecture refuses to cross.
- **Silent semantic plan reuse** — reuse without a human diff produces confidently
  wrong plans (already documented in cost-optimization.md §3.5). 6.1 is the safe
  version.
- **Letting agents push to app repos** (e.g. auto-adding data-testids) — suggestion
  artifacts only; the gate stays the only writer, and only to test repos.

---

## Priority: first two quarters

Impact-per-effort, dependency-ordered. Items marked ◐ depend on unblocking the
real-LLM parity run (Claude CLI auth) to be *measurable*, though buildable before.

| Order | Item | Why now |
|---|---|---|
| 1 | 1.1 CI auto-ingest | Every quality metric downstream starves without it; small |
| 2 | 1.5 Reviewer nudges | Acceptance rate is n/a because nobody is asked; small |
| 3 | 2.1 Extend-vs-create scout ◐ | The catalog is finally in context; make the decision explicit |
| 4 | 4.5 Adversary verdicts in UI | The challenge already happens; showing it is a small lift with big reviewer trust |
| 5 | 3.2 Risk-weighted gaps | Turns the gap list into a prioritized work source; small |
| 6 | 4.1 In-UI diff review | Biggest single reviewer-productivity win |
| 7 | 1.2 Flake quarantine | Uses 1.1; protects trust in the suite the platform builds |
| 8 | 3.1 Traceability matrix | The EM/audit artifact; mostly a join over existing data |
| 9 | 5.4 Scheduled maintenance | Converts a hand-run demo into an unattended service |
| 10 | 6.1 Similar-plan retrieval | The knowledge moat starts compounding here |

Strategic (quarter 3+): 5.2 multi-worker scale, 3.5 semantic search, 5.1 roles,
1.3 survival tracking, 3.3 required coverage checks.
