# Iterative Project Review — 2026-08-05

## Scope

Two-pass review of the platform documentation, core pipeline, adapters, feature
claims, and unit-test estate. Generated/demo/workspace artifacts were excluded.
The worktree was clean at the start of the review.

## Findings

| ID | Severity | File | Line | Finding | Impact | Action |
| --- | --- | --- | ---: | --- | --- | --- |
| R1 | P1 — fixed | `adapters/scm/github.sh`, `bitbucket.sh`, `stash.sh` | 12, 17, 35 | Clone URLs contained SCM tokens and clones are retained. | Credentials could persist in `.git/config` and appear in process arguments. | Fixed with token-free origins and an environment-backed Git credential helper; writable clones persist only the non-secret helper command for gate pushes. |
| R2 | P1 — fixed | `engine/pipeline.sh` | 382 | Real adapters cloned into persistent destinations without a safe refresh policy. | A second real run could fail because the destination already existed. | Fixed with `checkout_workspace.py`, which accepts only `src|tests` plus a validated repo name and clears that exact derived checkout. |
| R3 | P2 — fixed | `adapters/notify/slack.sh`, `tracker/jira.sh`, `scm/bitbucket.sh`, `scm/stash.sh`, `telemetry/splunk.sh` | various | HTTP write verbs treated 4xx/5xx responses as successful delivery. | Run records could claim comments, status, notifications, or telemetry were delivered when rejected. | Fixed with `curl --fail-with-body`; regression pins added. |
| R4 | P2 — fixed | `adapters/telemetry/splunk.sh` | 7 | Splunk always disabled TLS verification. | HEC token and telemetry were exposed to man-in-the-middle interception. | Fixed: verification is now default; `AIQE_SSL_VERIFY=0` is the explicit opt-out. |
| R5 | P2 — fixed | `engine/gate/spec_check.py` | 121 | Strict spec coverage was computed from the global generate contract, not the current target repo. | A scenario implemented only in another repo could make the wrong repo pass its gate. | Fixed: the merger now stamps trusted fan-out identity and the gate requires repo plus changed-file ownership before counting coverage. |
| R6 | P2 — clarified | `docs/product-roadmap.md` | 48 | “In-UI diff review” was marked fully shipped, but only a run-level note exists; per-hunk comments are absent. | Roadmap overstated the delivered review workflow. | Corrected to partial; side-by-side rendering and file/hunk-addressed comments remain backlog. |
| R7 | P2 — clarified | `docs/product-roadmap.md` | 45 | Flake quarantine was marked fully shipped, but UI quarantine and repair proposal are absent. | Operators cannot complete the proposed workflow in the dashboard. | Corrected to partial; UI actions, CI enforcement, and repair enqueue remain backlog. |
| R8 | P3 — fixed | `README.md` | 22 | README said 12 roadmap items shipped while the roadmap said 14. | Documentation was internally inconsistent. | Roadmap now distinguishes 12 fully shipped and 2 partial items; a currency pin checks the README count. |
| R9 | P2 — clarified | `REVIEW.md`, `CLAUDE.md`, `docs/product-direction.md` | various | Real-LLM parity was described as complete, blocked, simulated, and measured in different documents. | Readers could not establish the evidence state for current-head parity. | Docs now distinguish the successful historical Pass-5 run from the blocked current-head refresh; a version-stamped refresh artifact remains required. |
| R10 | P3 — clarified | `REVIEW.md` | 130 | A historical docs-audit note said dashboard counts were unified at nine without saying this described the then-current UI. | It read like a stale current claim beside the present dashboard. | The historical review now refers to the rendered view set at that revision without freezing another transient count. |
| R11 | P3 — fixed | `docs/architecture.md` | 6 | The authoritative architecture repeatedly described the original six-test-repository target estate, while the current registry contains three. | Readers could mistake the target/example topology for current inventory. | Current inventory is now identified as registry-driven (5 source, 3 test: 2 API/1 UI); fixed six-repo passages are labeled historical/target, and a registry-derived currency test prevents drift. |
| R12 | P2 — fixed | `engine/lib/work_queue.py`, Windows adapter tests | 46 | Native Windows PATH values were mixed with POSIX separators, and Git Bash then prepended its own tools after startup. | Test doubles were bypassed and adapter tests reached real network tools, producing false failures and unsafe accidental calls. | Added Git-Bash runtime path conversion plus post-startup PATH assignment; all affected Stash/Jira tests now use the shared invocation helper. |
| R13 | P2 — fixed | `bin/gen_agents_md.py` | 204 | Estate guidance was written directly and intermittently failed when Windows readers/indexers held the destination. | A successful registry mutation could report failure during derived-guidance regeneration; a crash could leave partial guidance. | Generation now uses a same-directory temporary file and the existing retried atomic replacement primitive. |
| R14 | P2 — fixed | `Makefile`, `.coveragerc`, `requirements.txt` | 18 | Python line/branch coverage had no report or enforced baseline; `make coverage` only measures catalog mapping. | Source coverage regressions were invisible. | Added terminal/XML/HTML Python coverage targets, wired the 67% measured floor into `make review`, documented the ratchet policy, and pinned the configuration. |

## Cross-File Integration Pass

| Boundary | Evidence traced | Result |
| --- | --- | --- |
| SCM adapters → persistent checkouts → gate push | Clone URL construction, credential helper, checkout replacement, pipeline call sites, security/repeatability tests | Token-free and repeatable; credentials are supplied only at request time. |
| Generate fan-out → merged contract → per-repo gate | Trusted repo identity, changed-file ownership, strict scenario accounting, two-repo pipeline test | Cross-repo scenarios can no longer satisfy the wrong repository's gate. |
| Registry → generated estate guidance → architecture docs | Registry inventory, `gen_agents_md.py`, architecture current-estate marker, documentation currency test | Runtime inventory and prose agree; historical rollout topology is labeled. |
| Python suite → Make release review → operator docs | Dependency/config, Make targets, audit-log isolation discovery, README/coding guide | Branch-aware coverage is enforced without replacing catalog-mapping coverage. |
| Native Windows pytest → Git Bash → adapter tools | Bash discovery, path conversion, post-startup ordering, conformance and adapter suites | Test doubles reliably precede real network tools; conformance remains green. |

## Product Backlog (not review defects)

The roadmap explicitly leaves survival tracking, assertion linting, selector
inventory, factories, required coverage policy, semantic catalog search, RBAC,
multi-worker execution, per-team routing, onboarding wizard, failure-pattern
memory, and curated-guidance suggestions unimplemented. Assertion-strength
enforcement and RBAC are the highest trust/security priorities. These are
intentional future product slices, not open correctness bugs in the reviewed
implementation.

## Unit-Test Coverage

- The suite now spans 102 pytest files; the authoritative executable count is
  pytest discovery rather than a prose-pinned function count.
- 43 of 72 `engine/lib` modules have a same-name focused test module; the other
  29 are exercised indirectly or sparsely.
- Full-suite Python coverage is **67.01% combined line/branch** across
  `engine/lib` and `bin`; `make python-coverage-check` enforces a 67% floor.
- `make coverage` remains the E2E catalog-mapping command. Shell/subprocess
  behavior remains outside Python's line metric and is covered by conformance
  and adversarial suites.
- Lowest measured areas are executable entry points that run in subprocesses
  (`gen_agents_md.py`, `repos.py`, `taskevent_receiver.py`, `run_record.py`, and
  `extract_contract.py`). They are the next coverage-ratchet targets.

## Validation

- `pytest registry/tests/test_adapter_http_failures.py -q`: **6 passed**.
- P1 clone security/repeatability suite plus adapter pins: **21 passed**.
- Existing clone idempotence, locked-workspace, and environment pins: **13 passed**.
- Spec-gate plus documentation-currency suites: **31 passed**.
- Trusted fan-out merge tests: **3 passed**.
- End-to-end two-repository fan-out pipeline: **1 passed**.
- Remaining-review regression batch (architecture currency, coverage config,
  Git-Bash adapter tests, audit-log isolation, generator mutation): **72 passed**.
- Full branch-aware pytest coverage gate: **1,269 passed in 12:08**; required
  67% reached, measured total **67.01%**.

## Review Closure

All P1–P3 correctness, security, portability, documentation-drift, and coverage
findings discovered in this review are fixed or explicitly clarified. No open
code finding remains in this review queue. Current-head real-LLM parity still
requires a version-stamped external run artifact; it is an evidence refresh,
not an unimplemented local fix. Future work is the product backlog above plus
raising the coverage floor as subprocess entry points gain focused tests.
