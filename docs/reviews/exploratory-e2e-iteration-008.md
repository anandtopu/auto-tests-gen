# Exploratory E2E Review — Iteration 008

## Scope

This iteration completed Feature 7, Spec workflow, against a served dashboard
with isolated synthetic requirements, structured scenarios, plan state and
waivers. It covered requirements approval, waiver expiry and matching,
add/remove controls, workflow state, drift, verification, and release-gate
behavior.

## Findings

| ID | Severity | Files | Finding | Impact | Fix |
| --- | --- | --- | --- | --- | --- |
| E2E-EXP-014 | P1 | `engine/lib/spec_store.py`, `engine/lib/waiver_store.py`, `bin/dashboard_server.py` | Waiver keys were joined directly beneath `AIQE_SPEC_DIR`; `../escaped-waiver` wrote a valid file outside the configured store. | An authenticated dashboard caller could write `waivers.yaml` into another writable directory; sibling requirements/spec paths shared the unsafe primitive. | Validate structured-spec keys centrally before every path, preserve total read semantics, and return 400 for invalid waiver API keys. |
| E2E-EXP-015 | P2 | `bin/dashboard.py` | Waiver save/remove and requirements approval passed data as bare fetch options, so the browser issued GET to POST-only routes. The waiver form also had no owner outside SSO. | Three advertised Spec workflow actions always failed with `not found`; even a corrected request would fail owner validation in the default local mode. | Send authenticated JSON POST options for all three actions and add an owner field; trusted SSO identity still overrides the submitted value. |

## Reproduction and retest evidence

- Before E2E-EXP-014, `POST /api/waivers/save` with key
  `../escaped-waiver` returned 200 and created
  `out/exploratory-e2e-iter8/escaped-waiver/waivers.yaml`. After the fix, the
  same real HTTP call returns 400, invalid GET returns 400, and a fresh escaped
  target remains absent. The focused server regression failed before the fix
  and passes afterward.
- Before E2E-EXP-015, clicking Add waiver returned `Refused: not found`; source
  inspection confirmed all three controls defaulted to GET. The three focused
  UI regressions failed before the fix. Afterward, the browser created
  `PROJ-301-S1`, rendered `Waiver saved.`, removed it with the gate-warning
  confirmation, and approved requirements with a signed-hash confirmation.
- A matching one-day waiver rendered `1d left`; the unmatched `PROJ-301-S99`
  rendered `MATCHES NOTHING` and returned an immediate save warning. A request
  without an owner returned 422 with the ownership rule.
- `spec_workflow.py --json` reported approved requirements, three scenarios,
  advisory/off governance, and the next plan action. `spec_drift.py check`
  reported a clean surface. `spec_verify.py PROJ-301` returned the documented
  no-mapping result rather than asserting that tests passed.

## Pass 1 — per-file review

- `engine/lib/spec_store.py`: one bounded ASCII key validator now guards spec,
  requirements, and waiver directories. Write paths fail before `pathlib`
  resolution; total read APIs translate invalid keys into their existing empty
  results, preventing new 500s while refusing traversal.
- `engine/lib/waiver_store.py`: API callers reuse the spec store's validator;
  expiry, unmatched, locking, atomic replacement and audit semantics are
  unchanged.
- `bin/dashboard_server.py`: waiver list/save/remove validate before any read or
  mutation and return 400 with no path disclosure. Authentication and SSO actor
  precedence remain intact.
- `bin/dashboard.py`: each mutation now supplies method, JSON content type and
  serialized body. The owner field makes the existing validation usable in
  token/local mode; SSO remains authoritative at the server boundary.
- Regression tests cover the real isolated server write, every spec-derived
  path, total invalid reads, and all three browser request contracts.
- Status and user documentation record only behavior proven through the served
  page, real APIs/CLIs, and adjacent automated suites.

## Pass 2 — cross-file review

- Correctness: the UI payload shape matches each existing POST handler; valid
  add/remove/approval transitions were browser-retested. Matching, unmatched,
  near-expiry and no-owner states remain distinct and actionable.
- Security: separators, absolute paths, trailing separators, overlong names,
  and invalid endpoint keys cannot reach derived spec paths. SSO identity
  overrides rather than trusts a user-supplied owner. No auth check was weakened.
- Reliability: all existing file locks and atomic replacements remain in place.
  Read helpers retain total return contracts, so malformed/untrusted keys do not
  turn containment into dashboard crashes.
- Deployment: there is no dependency, schema migration, environment, manifest,
  port or external-service change. Existing tracked specs remain valid under the
  bounded key grammar already used by plan state.
- Coverage: focused tests failed against the old behavior and pass after the
  fixes. The 204-test adjacent set covers gates, waiver expiry, drift, verify,
  API adversarial behavior, event audit and the UI; compilation and high-signal
  Ruff checks pass.

## Seed and cleanup review

The copied `PROJ-301` fixture and all mutated state live only under ignored
`out/exploratory-e2e-iter8`; they contain synthetic business data and no
credentials or PII. The browser tab and exact local server process are closed
at iteration end. Shared tracked specs and runtime stores were not mutated.

## Residual risk

- Real Slack notification, SCM clone and provisioned test execution are adapter
  integration concerns; drift and verification orchestration were exercised in
  mock/isolated mode and pinned by their focused suites.
- Verification intentionally returned `no cataloged tests map to this key` for
  the seed. Positive PASS/FAIL/unverifiable branches are covered automatically,
  but no live customer test repository was cloned.
- The canonical 1,591-test suite previously exceeded the bounded loop runtime;
  this iteration used the 204 closest checks plus compilation and Ruff.
- No blocker remains for Feature 7. The next least-covered slice is Feature 8,
  Trace.
