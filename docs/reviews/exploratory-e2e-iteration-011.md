# Exploratory E2E Review — Iteration 011

## Scope

This iteration completed Feature 10, Artifacts, through the real CLI,
authenticated API and served dashboard. It covered JIRA and PR artifact
selection, plan/scenario/test-data/generated-test evidence, structured and raw
diff rendering, coverage download, all export formats, mock external actions,
hostile persisted paths and missing-archive disclosure.

## Findings

| ID | Severity | Files | Finding | Impact | Fix |
| --- | --- | --- | --- | --- | --- |
| E2E-EXP-022 | P1 | engine/lib/app_paths.py, bin/qa.py, bin/dashboard.py | A gate diff path from persisted run data was opened after an unconstrained join with the checkout root. | A crafted or tampered run record could disclose any text file readable by the dashboard or CLI process. | Resolve only relative .diff paths whose canonical location remains inside reports/runs; visibly refuse unsafe paths and report missing archives. |

## Reproduction and retest evidence

- A deterministic numeric run record set its gate diff to
  C:\Windows\win.ini. Before the fix, qa.py artifacts PR-ART-9101 --full
  printed the file contents. Dashboard generation followed the same path.
- After the fix, the CLI prints unsafe diff path refused and no canary text.
  The served browser view displays Unsafe diff refused and states the required
  archive location.
- The valid PROJ-301 JIRA artifact still renders its plan, scenarios, test data,
  generated test metadata, validation, open question, clean source block,
  catalog sidecar and raw diff toggle. PR coverage remains downloadable.
- Markdown, HTML, DOCX and PDF exports returned 200 with their respective media
  types and non-empty bodies. Mock Confluence publish and JIRA attach returned
  successful responses without contacting production systems.

## Pass 1 — per-file review

- engine/lib/app_paths.py: run_diff_path rejects non-strings, empty values,
  absolute paths, traversal, canonical paths outside reports/runs and
  non-.diff suffixes. Canonical resolution also closes symlink escape.
- bin/qa.py: full artifact output uses the shared boundary and distinguishes
  unsafe from missing evidence. Normal summary output remains unchanged.
- bin/dashboard.py: the same resolver protects the HTML renderer; unsafe and
  missing evidence are escaped and conspicuous instead of silently omitted.
- Tests cover accepted archive paths and absolute, traversal, wrong-suffix and
  non-string rejections, plus a subprocess canary proving content is not read.

## Pass 2 — cross-file review

- Correctness: CLI and browser share one path contract. Valid archived diffs
  continue to parse into new, updated, deleted and catalog-sidecar blocks.
- Security: persisted run JSON is treated as data rather than filesystem
  authority. Both read surfaces now have the same canonical containment check.
- Reliability: absent evidence is reported explicitly; one unsafe record does
  not abort generation or hide all other artifact keys.
- Deployment: no dependency, migration, manifest, state schema or external
  service changed. Existing reports/runs relative diff values remain valid.
- Coverage: the focused resolver and subprocess regressions passed, then 187
  adjacent artifact, export, dashboard, adversarial, storage, reuse and bundle
  tests passed with compilation and high-signal Ruff checks.

## Seed and cleanup review

The hostile record used the existing ignored numeric-run pattern and referenced
a harmless operating-system compatibility file only long enough to demonstrate
the boundary failure. It was removed after retest. The browser tab and exact
local server processes were closed; the unrelated generated AGENTS.md remains
unstaged.

## Residual risk

- The organic PROJ-301 demo archive contains historical generated assertions
  that should not be treated as current production tests; this iteration
  verified artifact presentation, not regeneration of that archived fixture.
- Publish and attach were deliberately exercised against mock adapters. No
  external Confluence or JIRA service was contacted.
- No blocker remains for Feature 10. Feature 11, Activity and alerts, is next.
