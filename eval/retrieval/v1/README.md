# Retrieval gold set v1

This QE Platform-owned fixture set measures A3 retrieval before threshold tuning.
It contains 30 known changes and 30 testcase labels, split evenly across API
surfaces, UI routes, and non-URL helper/fixture/state changes.

`labels.json` pins the exact SHA-256 of `corpus.json`. Any corpus edit therefore
fails `make eval` until the QE Lead reviews the expected mappings and deliberately
updates the hash. Labels are reviewed on every corpus change and at least
quarterly. Semantic results are reported separately: real provider vectors are
measured and floor-enforced, mock hash vectors are labelled simulated, and an
unconfigured provider is explicitly unmeasured.

`hostile-testcase.json` is instruction-shaped retrieval data. The attack oracle
requires the data-framing preamble to retain tool, writable-scope, and gate
authority, and verifies that the deterministic gate does not consume this fixture.

`m9-baseline.json` deliberately records M9 as unmeasured until a real QA survey is
provided. Do not replace it with pipeline timing or a synthetic number: M9 is
QA-reported time-to-first-draft.
