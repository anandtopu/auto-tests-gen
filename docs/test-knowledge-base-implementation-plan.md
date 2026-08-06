# Test Knowledge Base & Agent Artifact Store — Implementation Plan

Date: 2026-08-05
Source: `docs/prd-test-knowledge-base.md` v2
Status: In implementation; A1–A3 implemented behind default-off flags

## 1. Delivery principles

- Every slice is controlled by the PRD's named flag and defaults to current
  behavior.
- Derived indexes remain rebuildable; durable artifact/run provenance does not.
- Deterministic signals run before semantic retrieval and may short-circuit it.
- An unavailable, unparsed, unmeasured, or skipped input is recorded explicitly.
- Retrieved test code is data, never instructions, and stays below an explicit
  prompt boundary.
- The gate remains the only path that writes or commits test-repository files.

## 2. Decisions made for implementation

| Decision | Resolution | Reason |
| --- | --- | --- |
| D1 parser | A dependency-free parser-adapter interface with a heuristic JS/TS adapter for Playwright and `node --test` in S1 | Those are the registered frameworks; parse statistics make blind spots visible and avoid a native dependency before evidence warrants it. |
| Duplicate case names | Add a deterministic occurrence suffix only when file + suite + title collide | The PRD's readable ID remains stable for normal cases while valid duplicate titles cannot overwrite one another. |
| Long cases | Treat one logical test case as one or more physical chunks with a shared `case_id` and numbered `part` metadata | This reconciles A1.1 with A1.4 without losing identity. |
| B1 addressing | Store immutable content blobs by SHA; store run-specific provenance as append-only references to those blobs | `produced_by_run` and `produced_at` cannot live in a deduplicated content object. |
| A4 timing | JIRA: check approved/draft scenarios before test generation. PR: check generated scenario/test contracts after generation but before comment/gate completion, advisory only | PR mode has no plan editor and no proposed scenario exists before generation. |
| A3 unaffected | Persist only scored candidates plus an explicit no-candidate result; do not enumerate the unbounded unaffected corpus | Keeps artifacts bounded and makes “unaffected” a candidate disposition, not a dump of every test. |
| A3/A5 sequencing | Ship A3 behind its default-off flag with provisional per-mode thresholds; A5 remains required before threshold tuning or default enablement | A3's deterministic contract and workflow integration are independently testable; claiming retrieval quality is not. |
| Baselines | Stamp every measured baseline with commit, mode, corpus hash, and timestamp | The prose snapshot already drifted from 28 to 30 chunks and from 13,318 to 15,036 AGENTS.md characters. |

## 3. Epic A — Test-aware knowledge base

### A1. Test-case-level indexing — Implemented

Dependencies: none beyond the current chunk store.
Flag: `AIQE_TESTCASE_INDEX=0` by default.

Implementation:

1. Add `engine/lib/testcase_parser.py` with an adapter-neutral result shape and
   the S1 JS/TS heuristic adapter.
2. Extract suite nesting, title, tags, endpoints/routes, selectors, page objects,
   helper/fixture references, assertion targets, and bounded body text.
3. Extend `knowledge_chunks.py` with the `testcase` kind. When enabled, parsed
   files emit case chunks; an unparsed file emits the existing file-level `spec`
   chunk with `parse_status=unparsed` and `parse_reason`.
4. Split chunks above `AIQE_TESTCASE_CHUNK_CHARS` (default 2,000), sharing one
   logical `case_id` and deterministic part numbering.
5. Add `make index-stats` with per-repo cases, physical chunks, parsed files,
   unparsed files, and reasons.
6. Make semantic exemplar ranking use testcase chunks while the flag is enabled.
7. Amend the closed chunk-kind pin and document the new flag/command.
8. Add framing and adversarial retrieval fixtures. The deterministic framing pin
   ships in A1; the behavior/quality attack evaluation ships with A5 in S2.

Validation:

- Nested Playwright and node-test fixtures yield one logical case per test.
- Duplicate titles remain unique; long cases split and stay within the limit.
- Unsupported/malformed files produce a visible fallback with a reason.
- Two rebuilds are byte-identical.
- Default-off output remains compatible with the existing seven-kind store.
- Retrieved hostile test text remains below the data-only boundary.

Implementation checkpoint (2026-08-05):

- Completed: heuristic parser, nested suite/case identity, metadata extraction,
  duplicate-title disambiguation, bounded multipart chunks, explicit unparsed
  fallback, per-repo `index-stats`, feature/settings wiring, testcase-aware
  exemplar ranking, and explicit test-code data framing.
- Verified: 11 A1 tests; 68 focused A1/compatibility tests; 113 retrieval,
  vector, context, settings, docs, and maintenance tests. Enabled estate smoke:
  6 logical cases / 6 chunks and one registered repo explicitly `NOT INDEXED`.
- Follow-on work remains explicitly owned by A6.1 (same-run gate indexing) and
  A5/S2 (the A1.6 behavioral attack evaluation); neither changes the shipped A1
  parser/index contract. A2 estate acquisition is implemented below.

### A2. Estate-wide indexing — Implemented

Dependencies: A1 parser; SCM adapter clone support.
Flag: `AIQE_TESTCASE_INDEX` (same S1 transaction).

Implementation:

1. Add an index checkout coordinator that resolves every registered test repo.
2. Reuse an existing `workspace/tests/<repo>` checkout when complete; otherwise
   use the Scm `clone_ro` verb into an isolated, safely replaceable index checkout.
3. Never infer “zero tests” from clone failure. Record `not_indexed` with SCM kind,
   exit class, and sanitized reason, then continue.
4. Feed the resolved roots into `knowledge_chunks.build()` rather than selecting
   whichever workspace/demo directory happens to exist.
5. Keep nightly rebuild before vector refresh so SHA skip embeds only changed
   chunks.

Validation: unavailable-repo degradation, mixed reachable/unreachable estate,
safe checkout replacement, no credential persistence, unchanged-vector zero-call.

Implementation checkpoint (2026-08-05):

- Added `engine/lib/index_checkouts.py`. A complete pipeline checkout is reused;
  every absent/incomplete registered test repo is acquired with its own
  registry-selected Scm `clone_ro` adapter into
  `reports/knowledge-index/checkouts/<repo>`.
- Clone preparation accepts only validated repository names and removes only the
  exact derived target. Failed partial clones are cleared before the build can
  inspect them.
- Every repository emits an `indexed` or `not_indexed` outcome on its
  `repo-surface` chunk, including source, SCM kind, exit class, and a bounded,
  credential-redacted reason. Failures are also named in rebuild output while
  the rebuild continues and exits successfully.
- `knowledge_chunks.rebuild()` passes resolved roots to the read-only builder.
  `make maintain` already orders this rebuild before vector refresh, retaining
  the existing SHA skip for unchanged chunks.
- Verified with mixed-estate, unavailable-SCM, invalid-checkout, partial-clone
  cleanup, credential redaction, per-repo adapter selection, rebuild wiring,
  narrow replacement, and maintenance-order tests.

### A3. Change-to-test impact analysis — Implemented

Dependencies: A1, A2, A5 measurement harness.
Flag: `AIQE_IMPACT_ANALYSIS=0`.

Implementation:

1. Introduce a versioned `impact-candidates.json` contract containing mode,
   query provenance, ranked candidates, signals, recommendation, confidence,
   threshold, and explicit no-candidate state.
2. Preserve `extend_scout` endpoint/route joins as the highest-priority signal.
3. Add deterministic identifier overlap between diff/ticket/scenarios and A1
   `exercises` metadata.
4. Query testcase vectors only when deterministic signals do not clear the
   configured mode-specific threshold.
5. Apply health/recency only as tie-breakers; never allow them to create a match.
6. Hook PR mode after diff/catalog preparation and JIRA mode after analyze/testplan.
7. Add bug-mode `should_have_caught` candidates and explicit absence.
8. Archive the artifact in run records and teach `explain` to render its reasons.

Validation: deterministic short-circuit makes zero embedding calls; lexical and
semantic thresholds remain separate; both trigger paths and bug absence are pinned.

Implementation checkpoint (2026-08-06):

- Added `engine/lib/impact_analysis.py` and the versioned, bounded
  `out/impact-candidates.json` proposal contract. Catalog surface and A1
  `exercises` joins run before retrieval; deterministic winners make no
  embedding call. Semantic retrieval falls back to lexical scoring and records
  the active mode plus independent thresholds.
- PR diff, JIRA authoring, plan review, and approved-plan resume run at lifecycle
  points where their complete input exists. The artifact joins generation only
  when the flag is enabled, is archived in the run record, and is rendered by
  `make explain` without borrowing another live run's artifact.
- Bug mode persists a ranked, surface-based `should_have_caught` answer or an
  explicit regression gap. Weak bounded candidates are marked `unaffected`; no
  threshold winner emits the PRD's explicit create-new message.
- The generate prompt treats candidate titles/reasons as untrusted retrieval
  data and the artifact states proposal-only authority. No impact code writes to
  test repositories; generation authors and the deterministic gate commits.
- Verified with 12 focused A3 acceptance/adversarial cases, 82 focused adjacent
  compatibility tests, and all 1,306 registry tests across three bounded
  tranches after fixing two broad-suite findings. The shell pipeline smoke was
  not runnable because this Windows host has no Git Bash/WSL `/bin/bash`.

### A4. Near-duplicate detection

Dependencies: A1, A3 query contract, selection/review state.
Flag: `AIQE_ARTIFACT_REUSE` during S5; advisory behavior is invariant.

Implementation:

1. Add a detector that compares proposed scenario text with testcase chunks and
   returns bounded candidates with mode/threshold recorded.
2. JIRA runs evaluate scenarios before test generation and surface warnings in
   the plan editor. PR runs evaluate the generated scenario/test contract before
   final reporting and surface warnings in the PR comment; they never suppress
   files or change gate status.
3. Add `duplicate` to scenario exclusion reasons and persist the referenced
   testcase `case_id`.
4. Add numerator and denominator fields required for M6.

Validation: false positives cannot block or delete; duplicate exclusions are
auditable; both UI/comment presentations name repo, file, suite, and case.

### A5. Retrieval quality measurement

Dependencies: QE-owned labels (D3); A1 fixtures before S3 tuning.
Flag: none; evaluation is always available.

Implementation:

1. Create a versioned labelled set of at least 30 API/UI/non-URL changes with
   corpus hashes and maintenance ownership.
2. Add precision@5, recall@5, and MRR evaluators for deterministic, lexical, and
   semantic modes separately.
3. Add a configured regression floor to `make eval` without treating an
   unconfigured semantic provider as zero quality.
4. Add the hostile-test fixture to the existing attack harness and assert that
   retrieved instructions cannot change tools, output scope, or gate authority.
5. Record the M9 human baseline before S3 is enabled.

Validation: metric math unit tests, label drift detection, mode separation,
attack mutation test, and an explicit `unmeasured` semantic state.

### A6. Close the learning loop

Dependencies: A1/A2 index coordinator, gate result, review/selection state.
Flags: S1 uses `AIQE_TESTCASE_INDEX`; outcome ranking waits for S5.

Implementation:

1. After each successful gate commit, parse changed committed specs and upsert
   their testcase chunks before the run record is finalized.
2. Append provenance events linking run, commit SHA, case IDs, and gate result.
3. On approval/changes-requested/duplicate exclusion, append outcome records;
   never rewrite chunk code or SHA.
4. Make ranking consume outcomes only behind the S5 flag and cap their weight so
   deterministic surface evidence remains dominant.

Validation: same-run retrieval, failed/no-change gates do not index, append-only
outcomes under concurrency, and code bytes remain unchanged by review decisions.

## 4. Epic B — Agent artifact generation and store

### B1. Durable content-addressed artifact store

Dependencies: `app_paths`, `fs_lock`, redaction rules.
Flag: `AIQE_ARTIFACT_STORE=0`.

Implementation:

1. Add `artifact_store.py` rooted at `AIQE_ARTIFACTS_DIR` or
   `reports/agent-artifacts/`.
2. Store immutable blobs at `blobs/<sha256>`; validate hash on read.
3. Store append-only references containing kind/scope/run/time/input SHA and the
   blob SHA. Identical blobs are shared across references.
4. Apply secret scanning, size ceilings, allowed-kind validation, atomic writes,
   `fs_lock`, corrupt-record quarantine, and test isolation from the first commit.
5. Mark-and-sweep unreferenced blobs after pruning bounded run references.

Validation: deduplication, provenance multiplicity, concurrent writers, read-only
rootfs path, redaction rejection, corruption, retention, and estate isolation.

### B2. Task artifact bundle

Dependencies: B1; run-record and state-bundle schemas.

Implementation:

1. Add a versioned bundle manifest per run referencing B1 hashes and explicit
   produced/skipped/fallback/unavailable states.
2. Capture estate/guidance/context/manifest/candidates/requirements/plan/skills at
   the points where they are actually consumed.
3. Resolve historical `make explain` answers from the manifest and verify hashes.
4. Include bundle references and blobs in full portable state; exclude run bundle
   manifests from knowledge-only export while retaining reusable non-run blobs only
   when their profile explicitly permits it.

Validation: historical explain after scratch deletion, missing-phase truthfulness,
portable round trip, tamper detection, and profile exclusions.

### B3. Artifact reuse across tasks

Dependencies: B1/B2; cost report; phase-cache attribution.
Flag: `AIQE_ARTIFACT_REUSE=0`.

Implementation:

1. Define per-kind canonical input manifests and hash them with generator version.
2. Reuse only pure context artifacts; explicitly deny workspace/git-producing
   phases and validate the deny set structurally.
3. Record one savings owner per avoided work unit (`phase_cache` or
   `artifact_reuse`, never both) and label token counts estimated unless supplied
   by a real provider.
4. Surface hit/miss/rejection reason in explain and cost reports.

Validation: stale input misses, generator-version misses, denied-kind rejection,
disjoint savings accounting, and no simulated dollar claim.

### B4. Structured application-repository facts

Dependencies: existing `repo_facts` schema and guidance generator.
Flag: presence of an authored app-repo facts file.

Implementation:

1. Generalize facts schema/validation from test repositories to a common base plus
   layer-specific harvested fields.
2. Deterministically harvest registry metadata, contract/routes, dependencies, and
   catalog evidence for opted-in app repos.
3. Merge authored/harvested tiers through the existing guidance generator; do not
   add another generator or change owned > curated > generated precedence.
4. Treat no authored file as today’s behavior and record unavailable harvest input
   separately from an empty surface.

Validation: backend/frontend fixtures, opt-in absence, deterministic rebuild,
precedence invariants, and no LLM/tool call.

## 5. Slice and release gates

| Slice | Stories | Required release evidence |
| --- | --- | --- |
| S1 | A1, A2, A6.1 | Parser matrix, estate coverage report, deterministic rebuild, same-run index, default-off parity |
| S2 | A5 + A1.6 | Labelled fixture review, metric floors, lexical/semantic split, injection attack, M9 baseline |
| S3 | A3 | Both trigger paths, bug mode, explain archive, precision/recall targets |
| S4 | B1, B2 | Concurrency/security/isolation suite, historical explain, state portability |
| S5 | B3, A4, A6.2–A6.3 | Advisory duplicate flow, append-only outcomes, disjoint cost attribution |
| S6 | B4 | Opt-in app facts, deterministic harvest, unchanged guidance precedence |

Global gate: full pytest/branch-coverage floor, adapter conformance, adversarial
suites, replay/context scorecard, feature-off behavioral parity, and documentation
currency pins.
