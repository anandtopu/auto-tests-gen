# Product Direction — AI QE Platform

*A product-manager's view: where this PoC sits in the 2026 market, why its shape is
right, and the shortest credible path from proof-of-concept to a product that is easy
to deploy, maintain, reliable and scalable. Companion to `docs/architecture.md` (how
it is built) — this document is about **what to build next and why**.*

---

## 1. Product thesis

**Turn the artifacts a team already produces — pull requests and Jira tickets — into
maintained, reviewed, committed end-to-end tests, with a human-visible chain from
intent to test to release.**

Four end-user jobs, all already working in this PoC:

| Job | Who feels the pain | What we ship today |
|---|---|---|
| PR → E2E tests | Developer, QA | Workflow A: diff-aware triage → generate/update in the *targeted* E2E repo → deterministic gate → commit + PR build status |
| Improve coverage from existing coverage | QA | Test catalog (born-mapped, evidence + confidence), coverage gaps (`[NO TEST]`) fed into every generation phase, extend-before-create bias |
| Review app repos & lift coverage | QA lead | Registry + harvested API/route surface, coverage matrix, per-repo generated AGENTS.md, repo guidance sync |
| Jira story/bug → test plan → tests | QA, EM | Workflow B plan-first: read description **and comments** → draft plan → human review/edit/**approval gate** → linked to Jira → generated tests |

And two visibility jobs:

| Visibility | Who | What we ship today |
|---|---|---|
| PR ↔ tests, story ↔ plan ↔ tests traceability | Engineering manager | Run records join trigger → phases → plan → generated diff → gate outcome → review status → release; surfaced in Runs/Artifacts views, team report, email digests |
| Coverage per PR, review of cases & plans | QA team | Review board (pending → approved/changes), rendered test code + before/after comparison, plan review/edit/approve UI, coverage matrix, advisory critic score |

## 2. Market landscape (researched July 2026)

The space splits into three clusters, none of which occupies our position:

| Cluster | Representative products | What they do | What they don't do |
|---|---|---|---|
| Autonomous E2E platforms | Meticulous (session replay on every PR), Momentic (natural-language tests), QA Wolf (service, 80% coverage claim), mabl / Autify / testRigor (low-code, self-healing) | Generate and maintain E2E coverage with humans reviewing | Tests live in **their** platform, not your repos; no Jira plan-first approval; weak self-hosted/DC story |
| Jira-side AI test cases | Xray AI, TestRail (Sembi IQ), Zephyr, TestStory.ai, marketplace generators | Story → manual/Gherkin test cases inside test management | Stop at *described* tests — no executable code, no execution gate, no commit to real E2E repos |
| PR-side code agents | Qodo (Merge + Cover), CodeRabbit, Diffblue | PR review, unit-test/coverage generation | Unit scope, not E2E; CodeRabbit has **no Bitbucket support**; none produce a story→plan→E2E chain |

**The whitespace this product occupies — defend all four:**

1. **Repo-native output.** Generated tests are committed to the customer's own E2E
   repositories, born-mapped into a catalog. No platform lock-in; tests outlive the
   tool. Every autonomous-E2E competitor hosts the tests themselves.
2. **Determinism where it matters.** The gate (lint → execute → secret-scan →
   born-mapped → push) is not an LLM and cannot be argued with; the LLM critic is
   advisory by construction. Competitors blend generation and judgment; we separate
   them, which is what makes the output trustworthy enough to commit.
3. **The full chain, human-gated.** Story → plan → *human approval* → data → tests →
   gate → review → release is one traceable object. Jira-side tools stop at cases;
   E2E platforms start at the app. Nobody else joins the two ends.
4. **Self-hosted Atlassian is a first-class citizen.** Bitbucket Server/DC (Stash)
   with per-repo project mapping, Jenkins triggers, no-OpenHands standalone mode, an
   OpenShift-ready image. The enterprise-DC segment is demonstrably underserved
   (CodeRabbit: no Bitbucket at all).

Sources: [awesome-ai-testing](https://github.com/tugkanboz/awesome-ai-testing) ·
[AI QA agents overview](https://ssojet.com/blog/est-ai-qa-agents) ·
[AI testing platform comparison](https://getautonoma.com/blog/ai-testing-platform-comparison) ·
[Shiplight 2026 tool survey](https://www.shiplight.ai/blog/best-ai-testing-tools-2026) ·
[Jira testing tools 2026](https://www.testrail.com/blog/jira-testing-tools/) ·
[AI test-case generators for Jira](https://testquality.com/ai-test-case-generators-jira-free-vs-enterprise-agents/) ·
[Qodo vs CodeRabbit](https://dev.to/rahulxsingh/qodo-vs-coderabbit-ai-code-review-tools-compared-2026-kdp) ·
[Bitbucket AI review tools](https://www.codeant.ai/blogs/bitbucket-code-review-tools)

Validation from industry: Meta's TestGen-LLM line of work established that
LLM-generated tests with **strict acceptance filtering** land in production code —
the same generate-then-deterministically-filter shape as our gate.

## 3. Personas → product priorities

**Developer** (consumes silently): the PR build status and the PR comment are the
whole product surface. Priority: keep signal precise — a wrong test that passes the
gate erodes trust faster than any missing feature. The critic's escaped-noise metric
is the health gauge; keep it ≤10%.

**QA engineer** (daily driver): plan review/edit/approve, test-case review with the
code comparison, coverage gaps. Priority: shorten review time — approve from the Jira
ticket itself (Forge/Connect app, H2), batch approvals, diff-only re-review when a
plan is edited.

**QA lead / EM** (weekly): traceability and trend. Priority: make the chain a
first-class page — one "Trace" view per key: story → plan (who approved, when) →
tests (files, actions) → gate → review → release; exportable for compliance. All the
data already exists in run records; this is a presentation gap, not a data gap.

## 4. Roadmap — three horizons

### H1 (0–3 months): pilot-ready — one team, real repos, real tickets
*Goal: a design partner runs Workflow A+B on their estate for 4 weeks with
acceptance ≥70% and zero gate escapes.*

- **Enforce the budgets.** ✅ *Shipped.* `MAX_COST_USD_PER_RUN` / `MAX_WALLCLOCK_MIN` were settings, not controls. Meter tokens per phase, hard-stop a runaway run. This is the
  #1 objection enterprise buyers raise about agentic tools.
- **Trace view** ✅ *Shipped* — dashboard view + `/api/trace` + `qa.py trace`.
- **PR comment with coverage delta** — the build status exists; add "3 behaviors
  covered, 1 open question" to the PR itself, where developers live.
- **Auth**: reverse-proxy SSO header support on the dashboard (full RBAC waits for H2).
- **Pilot metrics baked into the report**: acceptance rate, escaped noise, cycle time
  saved, cost/run — the scorecard already computes most; make them the pilot's
  weekly one-pager.

### H2 (3–6 months): team-scale — many repos, concurrent runs, state that scales
*Goal: 5–10 teams, concurrent runs, admin-free weeks.*

- **Concurrency = ephemeral runners, not a bigger box.** The one-run-per-checkout
  lock is correct *per workspace*; scale out by dispatching queue items to ephemeral
  K8s Jobs of the existing container (the OpenHands-sandbox pattern without
  OpenHands). The queue and per-repo parallel gates already exist.
- **State to PostgreSQL.** JSON-file stores (runs, reviews, queue, plans) are honest
  at PoC scale and a liability at team scale. Keep `fs_lock` semantics behind a thin
  store interface; migrate run records first (biggest file count), reviews second.
- **Jira-native surface.** A Forge/Connect app: plan approval and coverage panel on
  the ticket. This collapses the QA review loop and is where Xray/TestRail will
  compete — our answer is "and it's already executable code in your repo".
- **Flake and validity loop.** `health.json` ingest exists; add automatic quarantine
  proposals and mutation spot-checks to fill the scorecard's test-validity metric.
- **RBAC** (approver vs viewer), audit log on approvals and factory-type actions.

### H3 (6–12 months): platform
- Requirements-coverage reporting (AC ↔ test linkage is already in plans/catalog).
- Multi-estate/org rollout, packaging decision: self-hosted-first (our wedge) with
  managed SaaS as convenience, not the default.
- Deeper test-impact selection (run only affected E2E specs per PR) — natural
  extension of the catalog's evidence model.

## 5. Non-functional direction

- **Easy to deploy**: stays "one container + your credentials". H2 adds a Helm chart
  wrapping the existing OpenShift manifests and an optional Postgres dependency.
  Demo mode (zero credentials) remains the sales demo and the CI harness.
- **Easy to maintain**: the ports/adapters conformance suite is the contract — every
  new SCM/tracker ships with conformance tests or it doesn't ship. Generated files
  stay generated (`covers`, AGENTS.md, skills); hand-editable files stay small.
- **Reliable**: keep the invariants that made the PoC trustworthy — the gate is the
  only writer; idempotent triggers; plan approval revoked on edit; advisory critic
  never gates. Reliability regressions to date have all been state/lock/stale-server
  classes; the fixes (orphan-lock breaking, version banner, no-store, node --check)
  point the way: every failure mode gets a detector, not just a fix.
- **Scalable**: scale the *runners*, not the server. The server is a coordinator over
  a DB; runs are ephemeral pods; per-repo gates already parallelize inside a run.

## 6. Pilot KPIs (from the existing scorecard)

| KPI | Target | Today (demo estate) |
|---|---|---|
| Team acceptance of generated tests | ≥ 70% | 100% (3 decisions — needs real volume) |
| Escaped noise (critic) | ≤ 10% | 0% |
| Routing accuracy | ≥ 95% | 100% |
| Commit rate | informational | 99% of 189 runs |
| Cost per run | ≤ $2 single / $4 cross-repo | unenforced — H1 item |
| Cycle time vs manual authoring | ≥ 50% reduction | measure in pilot |

## 7. Top risks and mitigations

1. **Trust erosion from one bad test** → the gate + critic + plan-first are the
   product, not features; never trade them for speed.
2. **LLM cost/nondeterminism at scale** → budget enforcement (H1), per-phase model
   tiering (already in org-config), replay benchmarks (`make eval`) as regression
   harness for prompt changes.
3. **Incumbent bundling** (Atlassian/Xray shipping "good-enough" AI cases) → our
   moat is executable-and-committed, not described; accelerate the Jira-native
   surface (H2) so we meet them on their turf with a stronger artifact.
4. **Generated-test rot** → health ingest + flake quarantine (H2) and the update-
   before-create bias keep the estate curated rather than accreting.
