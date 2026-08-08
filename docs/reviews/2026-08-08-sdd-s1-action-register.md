# Action register: SDD-S1

| ID | Severity | Status | Area | Finding | Fix / evidence |
| --- | --- | --- | --- | --- | --- |
| S1-01 | P1 | Completed | Docs integration | Renamed nav left user guide and Settings hint on the old name | Updated both and extended currency pin |
| S1-02 | P1 | Completed | Test reliability | Standalone pipeline assumed every idempotent delivery is the first post | Assert the closed successful outcome set; 78-test rerun passes |
| S1-03 | P2 | Completed | Adversarial coverage | State-bundle hostile fixture omitted newly required manifest metadata and expected an obsolete return path | Require checksum-only preflight refusal; suite passes |
| S1-04 | P2 | Deferred | Visual validation | In-app browser runtime could not initialize because its asset path is missing | Rendered HTML and served HTTP/API passed; rerun visual inspection when browser runtime is repaired |
| S1-05 | P2 | Deferred | Windows coverage harness | Eight subprocess tests raised invalid-handle errors only inside coverage-wrapped full pytest | All eight passed in immediate isolated rerun; retain as harness residual |

No product-code P0–P2 item remains open.
