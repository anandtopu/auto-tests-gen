# SDD usability — implementation plan

Date: 2026-08-08
Source: [prd-sdd-usability.md](prd-sdd-usability.md) (Draft v2)

## Delivery order and status

| Order | Item | PRD mapping | Dependencies | Status | Implementation boundary |
| ---: | --- | --- | --- | --- | --- |
| 1 | SDD-S1 Vocabulary and state labels | A1.0–A1.2, A2.1–A2.2, A3.1–A3.2, M1 | none | Implemented | Presentation-only glossary/markup, exact state labels, machine-name disclosure, pinned user-facing docs |
| 2 | SDD-S2 Journey actions and refusal contract | B1.1–B1.2, B3.1, M2–M3 | SDD-S1 | Implemented | One action from `spec_workflow`, shared Python refusal builder, CLI/UI message parity |
| 3 | SDD-S3 Adoption levels | C1.0–C1.3, M5 | SDD-S1 | Implemented | One level mapping over existing resolved knobs, visible warn/strict sub-state, Custom truth |
| 4 | SDD-S4 Wizard and approval benefit | B2.1–B2.2, B4, M4 | SDD-S1, SDD-S2 | Pending | Conditional acceptance-criteria step and signed/prose-aware approval confirmation |
| 5 | SDD-FINAL Broad verification | M1–M6, risks, non-goals | SDD-S1–S4 | Pending | Full compatibility, mock journey, docs currency, final review and status reconciliation |

The sequence follows the PRD. Vocabulary ships first because every later
surface consumes it; refusal and journey contracts precede wizard composition;
adoption levels remain a mapping over existing knobs and do not create engine
semantics.

## SDD-S1 acceptance mapping

| Criterion | Implementation | Verification |
| --- | --- | --- |
| A1.0 | Separate signed structured approval from approved prose in term ids, labels, definitions, and newcomer copy | Exact vocabulary tests across UI guide, use cases, and getting started |
| A1.1 | Each marked term exposes its internal name/path; each state renders its machine name and `spec_workflow.py` location subordinate to the plain label | Rendered HTML and source-order pins |
| A1.2 | Apply the term policy to the journey UI and the three named newcomer documents in the same slice | Docs-currency test asserts the shared phrases in all three files |
| A2.1 | `glossary.py` owns closed definitions and safe `{{term:id}}` expansion; an explicit usage list makes reference/definition drift fail in either direction; ambiguous bare words are linted in new marked copy | Unit tests for both-direction coverage, undefined ids, escaping, and ambiguous-word lint |
| A2.2 | Every entry has separate one-sentence meaning and consequence plus greppable internal provenance | Shape/punctuation test and rendered tooltip inspection |
| A3.1 | Keep `spec_workflow.STATES`, `status`, `board`, and API output unchanged; dashboard consumes a presentation-only label map | Existing workflow/API suite plus exact machine-state tuple pin |
| A3.2 | Exact corrected labels cover all six states; blocker-fragment pins connect each label to the conditions in `spec_workflow.py` | Parameterized six-state source/behavior pin and rendered ordering check |
| M1 | All defined terms are referenced and all references resolve | Bidirectional glossary coverage test |

Implementation evidence: `glossary.py` now owns 15 meaning/consequence/internal
definitions, safe term markup, the five-sentence loop, and the exact six-label
presentation map. The dashboard keeps `specflow` and `spec_workflow` mechanics
unchanged while rendering plain labels first and machine names/paths on demand.
Focused SDD/dashboard tests passed 77/77; the adjacent SDD, wizard, UI, and docs
set passed 175/175; the mock standalone plus SDD regression set passed 78/78.
The missing-label mutation failed and its control passed. Real HTML generation
and local HTTP checks passed (page and six-state API both 200). Adapter,
adversarial, smoke, replay, context, discovery, retrieval, reviewer, and
scorecard stages passed after two stale cross-suite oracles were corrected.

`make review` reached 1,838 passing tests and the 70.99% coverage threshold but
its coverage-wrapped pytest process reported eight Windows invalid-handle
failures and one stale standalone expectation. The eight passed immediately in
isolation; the standalone expectation was corrected and retested. Browser-only
visual inspection remains blocked by a missing local browser runtime asset;
rendered HTML and served HTTP evidence cover the release path without claiming
that visual check passed.

## Later-slice acceptance summary

## SDD-S2 acceptance mapping

| Criterion | Implementation | Verification |
| --- | --- | --- |
| B1.1 | Every `spec_workflow` row computes one action label, equivalent command, and destination view; dashboard buttons consume those fields and the ticket key without branching on machine state | Six-state behavior pin, source mutation pin, served API rows all contain the three fields |
| B1.2 | Visible name remains **Plan → tests journey** and `data-view="specflow"` remains the machine id | Existing S1 currency/navigation pin remains green |
| B3 | `sdd_messages.py` owns five closed contracts: requirements, plan approval, uncovered scenario, expired waiver, and stale drift; each has what/why/one-action/command fields and one canonical text | Parameterized contract fixtures plus plan, gate, drift, queue, and dashboard integration assertions |
| B3.1 | `pipeline.sh` enters ticket plan/requirements gates through the builder CLI; `plan_state`, strict `spec_check`, `spec_drift`, queue failures, and dashboard APIs consume the same text | Direct Bash wiring pin; exact message equality tests; strict/warn truthfulness test |
| M2 | All five required refusal kinds are fixture-pinned; warn-mode coverage stays advisory and never says delivery refused | Five-kind parameterization and strict/warn captured-output test |
| M3 | All six states retain plain S1 labels and now expose one computed next action with its command | Six-state row fixture; real `/api/spec-workflow` returned six states and no row missing action metadata |

Implementation evidence: the combined dashboard/API/workflow/docs/adversarial
suite passed 354/354. Review fixes then passed 167/167 focused tests. Python compilation,
Ruff correctness checks, Bash syntax, dashboard generation, and a served local
HTTP/API check passed. The served board returned six states, one row, and zero
rows missing action metadata.

Two cross-file defects found during review were fixed: a stale scenario whose
vanished surface changed now updates evidence and re-notifies even when its id
does not change; coverage `warn` output remains advisory instead of falsely
saying delivery was refused. Computed dashboard refusal fields now win over
durable-entry keys and malformed legacy `stale_surfaces` degrades safely.

## SDD-S3 acceptance mapping

| Criterion | Implementation | Verification |
| --- | --- | --- |
| C1 | `adoption_levels.py` owns four names, one consequence each, and complete mappings over the three existing controls | Exact four-name/consequence and five resolved-tuple round-trip tests |
| C1.0 | Enforced coverage retains `warn` or `strict` as a visible sub-state; warn says “Dry run — reporting, not refusing” | Exact badge assertions plus real authenticated apply/GET round trip |
| C1.1 | `spec_workflow.governance()` asks the existing resolvers, attaches the derived level, and reports unmatched or unusable settings as Custom with raw resolved knobs | Mismatch tests and repeated invalid-value tests for all three controls |
| C1.2 / M5 | `updates_for()` returns exactly `AIQE_SPEC_MODE`, `AIQE_REQUIREMENTS_GATE`, and `AIQE_SPEC_ENFORCE`; the authenticated route passes that closed update atomically to `settings_store.save()` | Exact key mutation pin, route wiring pin, temporary `.env` integration assertion, malformed/adversarial API suite |
| C1.3 | Settings consumes the definitions API; Governance and Start here consume `governance().adoption`, including the same consequence | Shared-source pins, generated dashboard, governance JSON/markdown checks |

Implementation evidence: focused SDD and settings tests passed 73/73; the
authenticated adversarial server suite passed 115/115; the combined resolver,
workflow, settings, event-log, and API set passed 258/258. Python compilation,
isolated dashboard generation, and live governance JSON passed. Review and broad
compatibility evidence are recorded in the SDD-S3 review summary.

### SDD-S4

- Insert the criteria step only when the resolved requirements gate is on;
  preserve stable ladder labels across states.
- Make approval confirmation inspect structured/signature truth: signed plans
  state their protections; prose approvals state their exemptions.

## Product decisions and assumptions

| Question | Current implementation assumption | Completion rule |
| --- | --- | --- |
| Q1 new-estate default | **Reviewed plans**: retain the already-resolved `on/off/off` default, so S3 adds clarity without changing behavior | Product/QE may reverse to Off explicitly; no silent default migration |
| Q2 journey name | Use the PRD working title **Plan → tests journey** provisionally | Pilot comprehension probe may rename visible copy; machine id stays `specflow` |
| Q3 EARS visibility | Show the plain term and EARS expansion to current authenticated dashboard users | Role-specific hiding requires an explicit product/auth decision |
| Q4 governance terminology | Keep constitution clauses internal; add the shared adoption name/consequence as their operator-facing layer | Revisit only with an explicit product terminology decision |

## Iteration gate

Each run selects exactly one dependency-ready row, verifies the latest PRD and
prior push, refines its acceptance mapping, implements without changing SDD
mechanics, adds focused/adversarial tests, runs targeted then broad checks,
performs per-file and cross-file review, stages only item files, passes cached
diff checks, commits with the SDD item id, pushes, and verifies local/upstream/
remote parity before advancing.
