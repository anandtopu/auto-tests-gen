# PRD — Test Knowledge Base & Agent Artifact Store

| | |
|---|---|
| **Status** | Draft v2 — revised after adversarial gap review (Appendix B) |
| **Author** | Product Management (QE Platform) |
| **Date** | 2026-08-05 |
| **Epics** | **A** — Test-aware knowledge base (RAG over E2E test code) · **B** — Agent artifact generation & store |
| **Related** | [architecture.md](architecture.md) §5.13 · [cost-reduction-architecture.md](cost-reduction-architecture.md) · [knowledge-base-proposal.md](knowledge-base-proposal.md) · [spec-driven-architecture.md](spec-driven-architecture.md) |
| **Reviewers needed** | QE Lead (scope), Platform Eng (feasibility), Eng Manager (cost targets) |

---

## 1. Summary

QA engineers should be able to point this platform at a pull request, a JIRA story
or a bug, and get back **the right E2E tests — extended where a test already covers
the behaviour, created only where none does** — at a per-run token cost that falls
as the estate's knowledge grows rather than staying flat.

Two capabilities are missing to make that true:

**A. The platform cannot read its own test suites at the level a human reasons
about them.** The knowledge base indexes each spec file as one opaque blob. It can
tell you *that* `checkout.spec.js` exists and roughly what it is about; it cannot
tell you that `it("rejects a discount above the cap")` inside it is the test that a
PR changing the discount validator should extend. The decision "which tests need to
change for this PR" is currently made by joining catalog *evidence* (endpoints and
routes), which works for URL-shaped changes and is silent for everything else — and
it does not run on the JIRA path at all.

**B. The artifacts agents work from are rebuilt every time and thrown away.**
`AGENTS.md`, per-repo guidance, conventions files and the per-phase context
manifests are regenerated per run and overwritten by the next one. Nothing is
addressable, versioned, or reusable across tasks, so identical work is re-derived
and re-paid for, and a completed run cannot show what its agent was actually given.

This PRD specifies both, **as extensions to substrate that already exists** — chunk
store, Embedding port, vector index, retrieval-scoped context, phase cache, plan
reuse. It is explicit about what is already built (§4) so this document cannot be
read as a proposal to rebuild it.

---

## 2. Problem statement

### 2.1 The user's problem

A QA engineer receiving "PR #412 changes the discount service" must answer three
questions before writing a line of code:

1. **Does a test already cover this behaviour?** Today: open the E2E repos and
   grep. On six repos with thousands of tests, this is the single largest time sink
   and the reason duplicate tests get written.
2. **If yes, which test, and does it need extending or replacing?**
3. **If no, what does a new test look like *here*** — which fixtures, page objects,
   helpers and conventions does this repo use?

The platform answers (3) well (exemplar conventions are injected into every
authoring phase). It answers (1) and (2) only when the change is expressible as an
endpoint or route.

**Measured evidence:** the scorecard reports **update-vs-create: 0% of 280
generated tests extended an existing suite**. Every generated test to date has been
a new file. On a mature estate that number is a duplicate-test factory.

### 2.2 The business problem

Token cost per run is roughly constant. Every run re-sends estate knowledge
(`AGENTS.md`, currently ~3.3k tokens on a *demo* estate of 3 app + 3 test repos —
a real estate of 6 E2E repos and dozens of app repos scales this badly) and
re-derives conventions the platform has already derived hundreds of times. Cost
should decline per unit of output as the knowledge base matures. It does not,
because nothing durable accumulates between runs except the catalog.

### 2.3 Why now

The substrate landed in the cost-reduction and SDD streams (§4). The two gaps above
are what stands between "we have a retrieval stack" and "the retrieval stack
answers the question QA actually asks".

---

## 3. Goals and non-goals

### 3.1 Goals

| # | Goal | Measured by |
|---|---|---|
| G1 | The platform names the specific existing tests a change affects, at test-case granularity, on **both** the PR and JIRA paths | Extend-target precision/recall (§9) |
| G2 | `update-vs-create` moves off 0% | Scorecard, ≥40% on changes to covered surface |
| G3 | Retrieval returns *less* context, not more, while keeping every fact the phase needed | Context size reduction ≥50% with 100% expected-context retention |
| G4 | Every artifact an agent was given is durable, versioned and addressable | 100% of runs can reproduce their own context |
| G5 | Token cost per generated test falls as the corpus grows | Cost per accepted test, trend over 90 days |

### 3.2 Non-goals

- **Not** a general-purpose code search product. Scope is E2E test repositories and
  the application surface they exercise.
- **Not** replacing the catalog. The catalog remains the system of record for
  test↔app-repo mapping; this indexes *content* and joins to it.
- **Not** an LLM-judged mapping. Where a join is deterministic it stays
  deterministic — cheaper, reproducible, free.
- **Not** auto-approval. Retrieval informs authoring; the human approval gate and
  the deterministic quality gate are unchanged.
- **Not** editing test repositories outside the gate. Constitution C1 holds.
- **Not** extending plan-first mode to PR triggers. `pipeline.sh plan` is
  JIRA-keyed today; a reviewable *plan* authored from a PR is a workflow change
  orthogonal to the knowledge base. The ask is real and recorded (D7) — it is
  excluded here so this PRD stays one thing.

---

## 4. Current state — what already exists

**Read this section before writing any requirement.** Roughly 70% of the machinery
this PRD depends on is built and pinned.

| Capability | Module | State |
|---|---|---|
| Chunk store (7 kinds: repo-surface, guidance, exemplar, spec, scenario, catalog, testdata) | `knowledge_chunks.py` | **Built.** Byte-deterministic rebuild, gitignored derived data |
| Embedding port (OpenAI-compatible HTTP + mock) | `adapters/embed/*`, `embeddings.py` | **Built.** Conformance-tested; unconfigured → silent TF-IDF fallback |
| Vector index (SQLite float32 + cosine) | `vector_index.py` | **Built.** Changed-chunk-only refresh, daily embed budget, corrupt-db quarantine |
| Retrieval-scoped phase context (3 tiers: must-keep / token overlap / semantic fill) | `context_scope.py` | **Built.** On for triage, analyze, testdata; **off** for judgement phases pending quality eval |
| Semantic plan reuse from approved plans | `plan_reuse.py` | **Built.** Behind a flag, draft-only, deterministic adaptation |
| Content-addressed phase cache | `phase_cache.py` | **Built.** Excludes generate/validate by construction |
| Extend-vs-create targeting (PR path) | `extend_scout.py` | **Built.** Deterministic join on catalog evidence |
| Estate knowledge artifact | `gen_agents_md.py` → `AGENTS.md` | **Built.** Regenerated per run |
| Per-repo guidance (owned > curated > generated) | `repo_guidance_gen.py`, `curated_guidance.py`, `guidance_sync.py` | **Built.** Precedence pinned |
| Structured per-repo facts (authored + harvested tiers) | `repo_facts.py` | **Built.** `observed` tier deliberately not built — needs a real CI feed |
| Path-triggered agent skills | `gen_path_skills.py` → `.agents/skills/` | **Built.** Globs derived from repo layout |
| Spend controls, degradation ladder, provider-aware cost | `budget.py`, `cost_report.py` | **Built.** Simulated figures always labelled |

### 4.1 Measured baseline (this estate, 2026-08-05)

| Metric | Value | Note |
|---|---|---|
| Chunks indexed | 28 (spec=5, guidance=8, repo-surface=8, exemplar=3, catalog=2, scenario=1, testdata=1) | Demo estate |
| `AGENTS.md` | 13,318 chars ≈ 3,329 tokens | Injected into every LLM phase |
| Context-scope reduction | **29.3% avg**, retention OK | `eval/context_check.py`, 3 fixtures |
| Update-vs-create | **0%** of 280 generated tests | The number this PRD targets |
| Commit rate | 100% of 280 runs | Mechanics are sound |
| Cost per run | **Unmeasured** | All 280 runs simulated; `parity-*` blocked on CLI auth |

> **Honesty constraint carried from the platform's own rules:** every cost figure
> above is simulated and labelled. No target in §9 may be validated against
> simulated data. See §11-R4.

---

## 5. Gap analysis

| ID | Gap | Evidence | Impact |
|---|---|---|---|
| **GAP-1** | Spec chunks are **whole-file**. One `.spec.js` = one chunk containing the entire file. | `knowledge_chunks.py` — `_chunk("spec", repo, rel, path, p.read_text())` | Retrieval returns files, not tests. One large spec can consume the entire 4,000-token budget. Precision is bounded by file size. |
| **GAP-2** | Spec chunks are indexed **only for repos present in `workspace/tests/` or `demo/`** — i.e. whichever repos the last run happened to clone. | Same builder, `base = next(... workspace/tests, demo ...)` | The knowledge base's coverage depends on run history rather than on the estate. A repo not touched recently is invisible to retrieval. |
| **GAP-3** | No structural extraction from test code: no `describe`/`it` tree, tags, fixtures, page objects, helper usage, or assertion targets. | No parser exists | Cannot answer "which test case covers X", cannot rank by behavioural similarity, cannot detect near-duplicates. |
| **GAP-4** | Extend-vs-create targeting joins **catalog evidence only** (endpoints/routes) and runs on the **PR path only**. | `extend_scout.py` docstring: "JIRA paths are next-iteration" | Non-URL changes (validation rules, state machines, permissions, copy) produce no candidates. JIRA stories get none at all. |
| **GAP-5** | **No retrieval quality measurement.** `context_check.py` measures *retention* of expected context and size reduction — not whether the right tests were retrieved. | `eval/context_check.py` | We cannot tell an improvement from a regression, which makes tuning guesswork. |
| **GAP-6** | Generated artifacts are **scratch**: `knowledge/generated/` is gitignored and rebuilt; `out/context-<phase>.md` is overwritten by the next run. | Documented in `explain.py` — historical runs report context manifests *unavailable* | A completed run cannot show what its agent was given; nothing is reusable across tasks. |
| **GAP-7** | No **task-scoped artifact bundle** — no addressable object meaning "everything the agent needed for PR #412". | — | Re-running, auditing or handing a task to a different provider re-derives everything. |
| **GAP-8** | **No learning loop from outcomes.** Reviewer edits to generated tests are not captured anywhere, committed tests enter the index only when a later run happens to clone their repo, and review decisions never reach the chunks they judged. | `grep` for `reviewer_edit`/`human_edit` in `engine/lib/` → no matches; `repo_facts` `observed` tier deliberately unbuilt | The corpus grows in volume but not in quality signal. The cheap half (index on commit, record decisions) is pulled into scope as **A6**; capturing reviewer *edit diffs* stays out (D6). |

---

## 6. Users and jobs to be done

| Persona | Job | Today | With this |
|---|---|---|---|
| **QA engineer** | "A PR landed — tell me which tests to touch" | Greps six repos | Named test cases with a reason and a confidence |
| **QA engineer** | "Write a test that looks like ours" | Already good (exemplars) | Unchanged, plus retrieved near-neighbour tests |
| **QE Lead** | "Are we duplicating tests?" | No signal until review | Near-duplicate warning at authoring time |
| **QE Lead** | "Why did the agent do that?" | Explain panel, but context manifests expire | Durable per-run artifact bundle |
| **Eng Manager** | "Is this getting cheaper?" | Unmeasurable (simulated only) | Cost per accepted test, trended |
| **Platform Eng** | "Can I swap the embedding provider?" | Yes — port exists | Unchanged |

---

## 7. Epic A — Test-aware knowledge base

### A1. Test-case-level indexing

**Requirement.** The knowledge base SHALL index E2E test repositories at
**test-case granularity** — one chunk per `it`/`test`/scenario — carrying its
enclosing suite path, file, tags, and the identifiers it exercises.

*Rationale:* GAP-1, GAP-3. This is the single change that makes every downstream
capability possible.

**Chunk shape** (extends the existing 7 kinds; `chunk_id` stays content-independent
per the current contract):

```
testcase:<repo>:<file>#<suite-path>/<case-name>
  suite:        describe/context nesting, outermost first
  title:        the case name as written
  tags:         @smoke, @regression, fixture/annotation tags
  exercises:    endpoints, routes, selectors, page objects, helpers referenced
  fixtures:     data files and factories used
  assertions:   assertion targets (what is checked, not the literal code)
  body:         the case source, bounded (see A1.4)
```

**Acceptance criteria (EARS):**

- **A1.1** — WHEN the chunk store is rebuilt, THE SYSTEM SHALL emit one `testcase`
  chunk per test case in every registered E2E repository.
- **A1.2** — WHERE a test file cannot be parsed, THE SYSTEM SHALL emit a
  file-level chunk **and record the file as unparsed with the reason**, and SHALL
  NOT silently omit it. *(Constitution C13: an inability to establish a fact is
  never reported as an established negative — a file we could not read must not
  look like a file with no tests.)*
- **A1.3** — THE SYSTEM SHALL report parse coverage (`cases indexed`, `files
  unparsed`, per repo) via `make index-stats`, so a framework the parser does not
  understand is visible rather than quietly under-indexed.
- **A1.4** — THE SYSTEM SHALL bound any single chunk to a configurable maximum
  (default 2,000 characters), splitting a longer case rather than allowing one
  chunk to dominate the retrieval budget.
- **A1.5** — Rebuild SHALL remain byte-deterministic for identical inputs (pinned
  today; must not regress, or prompt-prefix caching breaks).
- **A1.6** — Indexed test code SHALL enter prompts only under the existing
  data-never-instructions framing, and the retrieval eval SHALL include an
  **adversarial fixture**: a test file carrying embedded instructions to the
  agent must be indexed faithfully AND provably not alter agent behaviour —
  asserted the way the gate's attack suite asserts, not by inspection. Indexing
  third-party-authored test files makes hostile content a retrieval payload;
  a constraint with no attack testing it is a constraint waiting to be lost.

**Open decision (D1):** parser strategy — regex/heuristic (cheap, framework-fragile)
vs. tree-sitter (accurate, adds a dependency the embeddings ADR deliberately
avoided). Recommendation: heuristic for JS/TS Playwright + `node --test` in slice 1,
with A1.3 making its blind spots visible; revisit if parse coverage <90%.

### A2. Estate-wide indexing, independent of run history

**Requirement.** Indexing SHALL cover every registered E2E repository regardless of
whether a recent run cloned it.

*Rationale:* GAP-2. A knowledge base whose coverage depends on which run happened
last is not a knowledge base.

**Acceptance criteria:**

- **A2.1** — WHEN a registered test repository is absent from the workspace, THE
  SYSTEM SHALL obtain its content through the existing Scm port (the same
  `fetch_file`/clone path `guidance_sync` uses) rather than skipping it.
- **A2.2** — WHEN a repository cannot be reached, THE SYSTEM SHALL record it as
  **not indexed, with the reason**, and continue with the rest. A single
  unreachable repo is degradation, not failure.
- **A2.3** — `make maintain` SHALL refresh the index nightly; only changed files
  SHALL be re-embedded (the existing sha-skip contract).

### A3. Change-to-test impact analysis

**Requirement.** Given a PR diff **or** a JIRA story/bug, THE SYSTEM SHALL produce
a ranked list of existing test cases the change affects, each with an
`extend | replace | unaffected` recommendation, a reason, and a confidence.

*Rationale:* GAP-4, G1, G2. This is the feature the user asked for, stated
precisely.

**Design constraint — hybrid, deterministic-first:**

| Signal | Source | Weight |
|---|---|---|
| Endpoint/route overlap | catalog evidence (`extend_scout` join, already built) | Highest — deterministic, free |
| Symbol/identifier overlap | `exercises` field from A1 vs. diff symbols | High — deterministic |
| Behavioural similarity | vector query over `testcase` chunks | Medium — costs embeddings |
| Recency/health | catalog `health.json` (quarantined tests rank down) | Tie-break |

**Acceptance criteria:**

- **A3.1** — THE SYSTEM SHALL run on both the PR and JIRA paths. On the JIRA path
  the query is the ticket's acceptance criteria and the authored scenarios; on the
  PR path it is the diff.
- **A3.2** — WHERE deterministic signals alone identify a target, THE SYSTEM SHALL
  NOT spend an LLM or embedding call. Cheap signals run first and can short-circuit.
- **A3.3** — WHEN no candidate clears the configured threshold, THE SYSTEM SHALL
  emit an explicit *"no existing test covers this — creating new specs is correct
  here"*, never an empty file. *(C13 again: silence and "nothing found" must not
  look alike.)*
- **A3.4** — Output SHALL be a named artifact joining the generate context, and
  SHALL be recorded on the run record so `make explain` can show why a test was
  extended rather than created.
- **A3.5** — THE SYSTEM SHALL NOT edit, delete or reorder any existing test. It
  proposes targets; the authoring phase writes; the gate commits. (C1.)
- **A3.6** — WHERE the trigger is a **bug** ticket, THE SYSTEM SHALL additionally
  answer *"which existing test should have caught this?"* — rank the cases
  covering the defective surface, and state explicitly when none does. That gap
  is the new regression test's justification, and it feeds the bug-specific
  issue-type guidance the pipeline already selects. A bug is not a story with a
  different label; treating them identically wastes the KB's best question.
- **A3.7** — Similarity thresholds SHALL be configured **per retrieval mode**
  (semantic vs. lexical fallback). The two score distributions are not
  comparable, and a single threshold tuned for embeddings silently misbehaves
  the day the endpoint is unconfigured. The mode in effect SHALL be recorded on
  the output artifact.

### A4. Near-duplicate detection

**Requirement.** Before generation, THE SYSTEM SHALL warn when a proposed scenario
is semantically near-identical to an indexed test case.

**Acceptance criteria:**

- **A4.1** — WHEN similarity to an existing case exceeds the configured threshold,
  THE SYSTEM SHALL surface the existing case (repo, file, case name) in the plan
  editor and the PR comment.
- **A4.2** — This SHALL be **advisory**. It never blocks the gate and never
  suppresses generation — a false positive that silently deletes a scenario is a
  worse failure than a duplicate test.
- **A4.3** — WHEN a reviewer excludes a scenario because a duplicate exists, the
  exclusion reason SHALL be recordable as `duplicate` in selection/review state.
  This is M6's instrumentation: without it, "duplicates reaching review" is a
  number nobody can produce.

### A5. Retrieval quality measurement

**Requirement.** Retrieval quality SHALL be measurable before any tuning is claimed
as an improvement.

*Rationale:* GAP-5. Without this, §9's targets are unfalsifiable.

**Acceptance criteria:**

- **A5.1** — A labelled fixture set SHALL exist mapping known changes to the test
  cases that should be retrieved (≥30 cases spanning API, UI, and non-URL changes).
- **A5.2** — `make eval` SHALL report **precision@5, recall@5 and MRR** against
  that set, and SHALL fail the build on a configured regression.
- **A5.3** — WHERE embeddings are unconfigured, the eval SHALL report the
  **lexical-fallback** numbers separately rather than blending the two.

### A6. Close the loop on what the platform itself produces

**Requirement.** Tests the platform generates, and the human decisions made about
them, SHALL enter the knowledge base without waiting for a future run to stumble
over them.

*Rationale:* the sponsor's stated end goal is knowledge *"built from learning and
test generation tasks."* Deferring the entire learning loop (GAP-8) to a follow-on
would ship a KB that grows only in volume. The expensive half — capturing reviewer
edit *diffs* — genuinely needs the CI feed and stays out (D6). The cheap half does
not, and is in scope here:

**Acceptance criteria:**

- **A6.1** — WHEN the gate commits generated tests, THE SYSTEM SHALL index them as
  `testcase` chunks **in the same run**. Today a committed test becomes retrievable
  only when a later run happens to clone its repo and the nightly refresh runs —
  meaning the platform's own most recent work is the one thing it cannot see.
- **A6.2** — WHEN a review decision is recorded for a key (approve /
  changes_requested / a `duplicate` exclusion per A4.3), THE SYSTEM SHALL record
  the outcome against the chunks that run produced, and ranking MAY prefer
  patterns from accepted runs.
- **A6.3** — Outcome recording SHALL be append-only provenance on the chunk
  metadata, never a mutation of the indexed content — the chunk stays a faithful
  copy of the code; what changes is what we know about it.

---

## 8. Epic B — Agent artifact generation and store

### B1. Durable, versioned artifact store

**Requirement.** Every artifact generated for an agent SHALL be written to a
content-addressed store with provenance, and SHALL survive the run that made it.

*Rationale:* GAP-6. Today `explain` must tell an auditor that a historical run's
context manifest is *unavailable* — honest, but a gap.

**Artifact kinds in scope:** estate `AGENTS.md`, per-repo `AGENTS.md`/`CLAUDE.md`
(generated tier only — owned files are never stored or shipped), conventions
/exemplar files, per-phase scoped context + manifest, extend-candidate lists,
requirements and plan renderings, generated skills.

**Acceptance criteria:**

- **B1.1** — Each artifact SHALL be stored under a `sha256` of its content with
  `{kind, repo|key, produced_by_run, produced_at, inputs_sha}`.
- **B1.2** — Identical content SHALL be stored **once** and referenced by many runs.
- **B1.3** — Retention SHALL be bounded and configurable, pruned by `make maintain`
  alongside run records.
- **B1.4** — THE SYSTEM SHALL NOT store secrets. The existing redaction denylist
  and length ceiling apply; a bundle-style export SHALL exclude `.env` and
  properties files (non-negotiable, per the state-bundle contract).
- **B1.5** — A repo-owned `AGENTS.md`/`CLAUDE.md` SHALL never be overwritten,
  outranked, or pushed to the repo. (Constitution: generated guidance never
  outranks owned guidance; the gate is the only writer.)
- **B1.6** — The store SHALL live on the deployed shape's writable surface:
  under the volume-mounted `reports/` tree, or a path resolved through
  `app_paths`. (The chunk store already complies — `reports/knowledge-index/`
  sits on the reports volume in both deployments, which is why `resolve_rel`
  deliberately does not relocate it. A new store placed under an unmounted,
  unrelocated path would fail on a read-only rootfs — visibly on a cluster,
  invisibly on a dev checkout, which is the dangerous half.)
- **B1.7** — Mutations SHALL go through `fs_lock`, and the module SHALL join the
  existing build pin that fails on an unlocked read-modify-write. Store writes
  happen from parallel per-repo gates and generate fan-out; "it worked in the
  demo" is single-writer luck.
- **B1.8** — The store SHALL ship with its isolation knob (`AIQE_ARTIFACTS_DIR`)
  honoured from day one, redirected by the test conftest, and covered by the
  class-level pin (`test_no_writable_state_store_still_points_at_the_estate`).
  Five prior stores leaked test traffic into the operator's estate before their
  knobs existed; a sixth would be a process failure, not an accident.

### B2. Task artifact bundle

**Requirement.** Each run SHALL produce one addressable bundle describing
everything its agents were given.

*Rationale:* GAP-7. This is what makes a run reproducible, auditable and
transferable between providers.

**Acceptance criteria:**

- **B2.1** — The bundle SHALL reference (not duplicate) stored artifacts by hash.
- **B2.2** — `make explain KEY=…` SHALL answer from the bundle for **historical**
  runs, replacing today's *unavailable*.
- **B2.3** — WHERE an artifact was not produced (a skipped phase, a scoping
  failure that fell back to the full estate), the bundle SHALL say so explicitly
  rather than omitting the entry.
- **B2.4** — The bundle SHALL be included in the portable state bundle so a new
  deployment inherits the knowledge, and SHALL be excluded from the
  knowledge-only profile where it would carry run history.

### B3. Artifact reuse across tasks

**Requirement.** WHEN a new task's inputs hash to artifacts already in the store,
THE SYSTEM SHALL reuse them instead of regenerating.

*Rationale:* the direct cost lever. Two PRs against the same repo on the same day
regenerate identical conventions and estate knowledge today.

**Acceptance criteria:**

- **B3.1** — Reuse SHALL be keyed on the **content of the inputs**, so a stale hit
  is impossible and no TTL is needed (the phase-cache contract, reused).
- **B3.2** — Each reuse SHALL be counted and reported in the cost report as
  `artifacts_reused` with the tokens avoided — **counted once**. A reuse already
  claimed by the phase cache is not also artifact reuse; the two lines stay
  separate and disjoint in the report. Summing overlapping savings would inflate
  the number this PRD is judged by, which is the iron rule's territory.
- **B3.3** — Reuse SHALL never apply to artifacts whose product is files in a
  workspace or git state — the same exclusion that keeps `generate`/`validate` out
  of the phase cache. Replaying a contract would hand the gate a green report for
  work that never happened.

### B4. Structured facts for application repositories

> **Corrected in v2.** The v1 draft claimed app repos get no generated guidance.
> That was wrong: `repo_guidance_gen.py` covers source repositories too — every
> registered app repo already receives a generated `AGENTS.md` on add and on
> demand. What app repos actually lack is only the **structured facts tier**:
> `repo_facts.py` deliberately models E2E test repos alone, so a team cannot
> assert must/should/avoid conventions or pitfalls about an app repo in the
> machine-readable form the phases consume. This requirement is therefore
> narrow, and materially cheaper than v1 implied.

**Requirement.** The authored + harvested facts split (`knowledge/facts/`) SHALL
extend to application repositories where a team opts in.

**Acceptance criteria:**

- **B4.1** — Harvesting SHALL be deterministic (registry + harvested contract +
  route table + catalog evidence). No LLM call.
- **B4.2** — Absence SHALL remain normal: a repo with no facts file behaves
  exactly as today. Adoption is per-repo.
- **B4.3** — The existing generated-guidance path is unchanged. This adds
  structure to what a team can assert; it SHALL NOT introduce a second guidance
  generator beside the one that exists.

---

## 9. Success metrics

| # | Metric | Baseline | Target | Method |
|---|---|---|---|---|
| M1 | Extend-target **precision@5** | n/a (no measurement) | ≥0.80 | A5 labelled set |
| M2 | Extend-target **recall@5** | n/a | ≥0.70 | A5 labelled set |
| M3 | **Update-vs-create** | **0%** | ≥40%, **conditioned**: denominator = tests generated by runs that had ≥1 extend candidate above threshold (recorded per A3.4) | Scorecard, conditioned on the run record's candidate list |
| M4 | Context size reduction | **29.3%** | ≥50% | `context_check.py` |
| M5 | Expected-context retention | 100% | **100% — non-negotiable** | `context_check.py` |
| M6 | Duplicate tests reaching review | unmeasured | −50% after baseline | `duplicate` exclusions in review state (A4.3 — the instrumentation ships with the feature, or this row is theatre) |
| M7 | Artifact reuse rate | 0% | ≥60% of artifact-tokens avoided | Cost report (B3.2), disjoint from phase-cache savings |
| M8 | **Cost per accepted test** | **unmeasurable today** | −40% over 90 days | Cost report — **gated on §11-R4** |
| M9 | Time-to-first-draft (QA-reported) | unmeasured | −30% vs. a baseline **collected during S1, before any feature ships** | Quarterly survey |

An unconditioned M3 would be gameable: a flood of tests on uncovered surface
(where creating is correct) would dilute the rate without a single extension
happening. The condition ties the metric to the decisions the feature actually
influences.

**Guardrail metrics** — a regression in any of these invalidates a win elsewhere:
critic score (≥0.86), commit rate (100%), gate exit-code distribution unchanged,
p95 run wall-clock (retrieval must not trade cost for latency). **Caveat the
guardrails honestly:** today's critic scores and commit rate derive entirely from
simulated runs, so until parity produces real runs they guard *mechanics*, not
output quality. A guardrail "pass" before then is a statement that nothing broke,
not that nothing got worse.

> **M8 cannot be validated on this estate today.** All 280 recorded runs are
> simulated; `make parity-pr` / `parity-jira` are blocked on Claude CLI auth. The
> platform's iron rule is that a simulated figure is never presented as a measured
> one, so M8 stays *unmeasured* — not zero, not estimated — until parity runs
> produce real spend. See §11-R4.

---

## 10. Delivery plan

Each slice ships behind a **named flag, default off**, is independently
verifiable, and defaults to today's behaviour when disabled — the platform's
established rollout pattern (`AIQE_PLAN_REUSE`, `AIQE_CONTEXT_SCOPE`).

| Slice | Scope | Flag | Exit criteria |
|---|---|---|---|
| **S1 — Test-case indexing** | A1, A2, A6.1 | `AIQE_TESTCASE_INDEX` | Chunks at case granularity for every registered repo; parse coverage reported; rebuild still byte-deterministic; committed tests indexed same-run; index-stats target. **Includes the deliberate `KINDS` pin amendment** — the closed-kinds set is closed by test, so adding `testcase` breaks the pin *by design*; amending it is part of the slice, not a test fix-up discovered in CI |
| **S2 — Measurement first** | A5, A1.6 eval | — (eval, always on) | Labelled fixture set + precision/recall/MRR in `make eval`, incl. the injection fixture. **Ships before tuning**, so S3 can be judged. M9 baseline survey collected here |
| **S3 — Impact analysis** | A3 | `AIQE_IMPACT_ANALYSIS` | Ranked extend targets on both paths incl. the bug mode; deterministic signals short-circuit; per-mode thresholds; explicit "no candidates"; recorded for explain |
| **S4 — Artifact store** | B1, B2 | `AIQE_ARTIFACT_STORE` | Content-addressed store honouring B1.6–B1.8 (placement, locking, isolation) from the first commit; historical explain answers from the bundle; retention pruning |
| **S5 — Reuse + cost surfacing** | B3, A4, A6.2–A6.3 | `AIQE_ARTIFACT_REUSE` | Reuse counted once and reported; near-duplicate warnings advisory-only; review outcomes recorded against chunks |
| **S6 — App-repo facts** | B4 | — (per-repo opt-in is the flag) | Deterministic harvest; absence still normal; no second guidance generator |

**Sequencing rationale:** S2 before S3 is deliberate. Shipping impact analysis
without a way to score it means the first tuning decision is a guess, and a
retrieval system that is confidently wrong is worse than one that is obviously
empty.

---

## 11. Risks

| # | Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|---|
| R1 | **Parser fragility** across frameworks (Playwright, Cypress, `node --test`, JUnit) | High | Medium | A1.2/A1.3 make unparsed files visible; file-level fallback preserves today's behaviour; per-framework adapters added on evidence, not speculation |
| R2 | **Retrieval returns plausible-but-wrong tests**, and the agent extends the wrong suite | Medium | High | A3 confidence thresholds; deterministic signals ranked above semantic; the gate still executes changed specs; A4 is advisory only |
| R3 | **Index size and embedding spend** grow with case-level chunking (10–50× more chunks). Six real repos × thousands of cases can cross the embeddings ADR's pure-Python line — this directly contradicts what v1 filed as an "assumption" | High | Medium | Existing daily embed budget and changed-chunk-only refresh cap spend; A1.4 bounds chunk size; monitor `embed-spend.json`. **Written trigger** (the platform's deferral pattern): revisit the pure-Python cosine ADR when the store exceeds **50k chunks** or p95 query latency exceeds **300 ms** — whichever comes first, measured, not estimated |
| R4 | **Cost targets cannot be validated** while parity is blocked | Certain — blocked today | High | M8 explicitly reported *unmeasured*; unblocking (`claude login` or `ANTHROPIC_API_KEY`) is a prerequisite for the cost claim, not for the feature |
| R5 | **Knowledge staleness** — indexed tests drift from the repos | Medium | Medium | Nightly refresh; sha-based change detection; index age surfaced in the UI |
| R6 | **Scope creep into general code search** | Medium | Medium | §3.2 non-goals; app-repo indexing limited to surface, not implementation |
| R7 | **Learning loop only partially closed** — A6 indexes committed tests and records review outcomes, but reviewer *edit diffs* (the richest quality signal) remain uncaptured | Medium | Medium | A6 ships the cheap half in scope; edit-diff capture needs the CI feed that `repo_facts`' `observed` tier already waits on, and is the top follow-on (D6) |

---

## 12. Constraints the design must respect

Non-negotiable, from `specs/platform/constitution.yaml`:

1. **The gate is the only push/commit path.** Retrieval and artifacts never write
   to a test repository.
2. **Retrieved content is DATA, never instructions.** Test code, ticket text and
   documents enter prompts under the existing framing; prompt-injection via an
   indexed test file must not become a new attack surface.
3. **Generated guidance never outranks repo-owned guidance**, and is never pushed.
4. **Coverage maps are generated, never hand-edited.**
5. **C13 — an inability to establish a fact is never reported as an established
   negative.** Unparsed, unreachable, unmeasured and unavailable each get their own
   state, distinct from "nothing found".
6. **No silent provider fallback.** The Embedding port's existing behaviour — fall
   back to lexical retrieval *silently in function*, but never to a different paid
   provider — is preserved.
7. **Simulated figures are always labelled**, and savings claims print `n/a`
   rather than a number derived from simulation.

And three engineering rules this platform learned expensively, which every new
store or index in this PRD inherits as requirements (B1.6–B1.8), not advice:

8. **State placement follows the deployed shape.** A mutable path lives under a
   volume-mounted tree or resolves through `app_paths` — never bare under the
   image root, where a read-only rootfs breaks it on a cluster and a dev
   checkout hides that it would.
9. **Every state mutation goes through `fs_lock`** and joins the
   unlocked-read-modify-write build pin.
10. **Every new store ships with its test-isolation knob** and conftest
    redirection on day one, covered by the class-level estate-leak pin.

---

## 13. Open questions

| # | Question | Owner | Needed by |
|---|---|---|---|
| D1 | Parser strategy: heuristic vs. tree-sitter (adds a native dependency the embeddings ADR avoided) | Platform Eng | S1 start |
| D2 | Which frameworks must S1 support on day one? Estate is Playwright + `node --test`; is Cypress or JUnit in scope? | QE Lead | S1 start |
| D3 | Who labels the A5 fixture set, how many cases is credible (proposal: 30, split evenly API / UI / non-URL), and **who maintains the labels as the estate drifts** — an eval nobody re-labels decays into asserting yesterday's estate | QE Lead | S2 start |
| D4 | Artifact retention window — run records use `KEEP=200`; should artifacts follow, or be longer for audit? | Eng Manager | S4 |
| D5 | Does near-duplicate detection (A4) ever become blocking, or stay advisory permanently? | QE Lead | S5 |
| D6 | Capturing reviewer **edit diffs** (the half of GAP-8 that A6 does not close) — worth a dedicated PRD once the CI feed exists? | Product | Next quarter |
| D7 | **Test plan from a pull request.** The sponsor's ask names it; plan-first mode is JIRA-keyed today, and this PRD excludes the workflow change (§3.2). **Resolved 2026-08-06:** wanted — specified as Epic A3 of [prd-pr-jira-fused-context-multi-agent.md](prd-pr-jira-fused-context-multi-agent.md) | Product + QE Lead | ~~Next planning cycle~~ done |

---

## 14. Dependencies and assumptions

**Dependencies**

- Embedding endpoint configured (`EMBED_URL`/`EMBED_MODEL`) for semantic ranking.
  Unconfigured, the system degrades to lexical retrieval — functional, less precise.
- Scm port access to every registered E2E repository (A2.1).
- CI results ingest for health-based ranking (already built; currently 0 tests
  tracked on this estate).
- **Claude CLI auth** for any real cost measurement (R4).

**Assumptions**

- E2E repositories are structurally conventional enough for heuristic parsing
  (`describe`/`it` or equivalent). D2 tests this assumption.
- The catalog's test↔app-repo mapping is accurate enough to anchor the
  deterministic half of A3. Bootstrap tiers below 0.85 confidence already go to a
  human review queue.

*(v1 assumed the estate stays under ~50k chunks. Case-level indexing is precisely
what threatens that, so it is no longer an assumption — it is risk R3 with a
written trigger.)*

---

## Appendix A — Worked example

**Input:** PR #412 — `orders-api`, changes discount-cap validation from 50% to 40%
and adds a stacking rule. No route or endpoint changes.

**Today:** `extend_scout` joins on endpoints and routes. The endpoint is unchanged,
so no candidates are produced. Generation writes a new spec file. Update-vs-create
stays at 0%, and a reviewer discovers the near-duplicate at review time — or does
not.

**With this PRD:**

1. Diff symbols: `DISCOUNT_CAP`, `applyDiscount`, `stacking`.
2. A3 deterministic pass: `exercises` on `testcase` chunks matches `applyDiscount`
   → 2 cases in `e2e-api-tests-1`.
3. A3 semantic pass: "discount boundary rejection" ranks a third case.
4. Output: **extend** `orders/discount.spec.js#applies a discount → rejects above
   the cap` (0.91, symbol + behaviour match); **extend** `…#stacks with a promo`
   (0.78); **create** one new case for the stacking rule with no existing coverage.
5. A4: the proposed "rejects above cap" scenario is 0.94 similar to an existing
   case → surfaced in the plan editor before a human approves.
6. B3: conventions and estate artifacts for `e2e-api-tests-1` hash-match this
   morning's run → reused, not regenerated. Tokens avoided are counted.

**Result:** two extensions and one new test instead of three new tests, and the
context sent to the authoring phase is smaller and more specific.

---

## Appendix B — Revision history

**v2 (2026-08-05)** — after an adversarial gap review of v1, verified against the
codebase rather than the draft's own claims. Two review findings were themselves
corrected during verification, which is why this appendix exists: the review is
part of the record, including where it was wrong.

| Change | Driven by |
|---|---|
| B4 rewritten. v1 claimed app repos get no generated guidance; `repo_guidance_gen.py` covers source repositories, so they already do. Scope narrowed to the structured-facts tier only | Review finding 1 — v1 proposed building something that exists, the exact failure §4 exists to prevent |
| Dangling "GAP-9" reference removed | Finding 2 |
| A6 added: index committed tests same-run, record review outcomes on chunks | Finding 3 — the sponsor's goal names *learning*; v1 deferred all of it |
| Plan-from-PR: explicit non-goal + D7 | Finding 4 |
| A3.6 bug mode ("which test should have caught this") | Finding 5 |
| B1.6–B1.8 + constraints 8–10: placement, locking, test isolation | Findings 6–8. **Finding 6 was withdrawn as a defect claim**: v1's reviewer asserted the chunk store's un-relocated path was a live bug; verification against the deploy manifests showed `reports/` is volume-mounted in both deployments, so the placement is the design. The requirement survives; the bug report does not |
| M3 conditioned, M6 instrumented (A4.3), M9 baselined, guardrails caveated as simulated | Findings on unmeasurable metrics |
| A1.6 injection fixture in the eval | Finding 9 — a constraint with no attack testing it |
| A3.7 per-mode thresholds | Finding 10 |
| 50k-chunk assumption converted to risk R3 with a written trigger | Finding 11 |
| B3.2 single-count rule vs. phase cache | Finding 12 |
| Named flags per slice; `KINDS` pin amendment named in S1; A5 label maintenance in D3 | Finding 13 |
