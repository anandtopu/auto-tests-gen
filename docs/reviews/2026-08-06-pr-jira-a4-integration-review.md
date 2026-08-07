# A4 Discovery Evaluation — Cross-File Integration Review

## Scope

Trace: QE labels → pinned synthetic SCM/Tracker fixtures → production
`ticket_discovery.extract/resolve` → discovery-quality artifact → `make eval`
and scorecard.

## Findings

| Dimension | Result |
|---|---|
| Correctness | Metrics consume only Tracker-valid candidates. All production signals are independently supported; absent and invalid outcomes stay distinct; ambiguity refusal is a positive decision rather than a miss. |
| Security | Fixture references are confined to the version directory, fixture bytes are SHA-256 pinned, label keys use the production grammar, and no fixture text reaches an LLM, adapter, gate, queue, or run store. |
| Reliability | Missing/extra validation labels, label drift, signal drift, final-outcome drift, and threshold regression fail the evaluator. Fixture identity prevents repeated ticket keys from collapsing samples. |
| Product behavior | A4 changes only always-on evaluation tooling; runtime discovery remains behind its existing default-off flag. |
| Compatibility | `make eval` and `make review` add one deterministic Python step. Existing replay, context, retrieval, and scorecard outputs remain intact. |
| Deployment | No runtime dependency, adapter, port, service, store, migration, environment variable, or container change. |
| Coverage | Ownership/hash/schema, all required scenarios, per-signal math, invalid validation, conflict refusal, wrong guesses, repeated tickets, path escape, threshold weakening, CLI artifact, Make integration, and documentation discoverability are covered. |

## Validation

The integrated evaluation chain and full registry suite are green. No open
P0/P1/P2 finding remains.

## Residual Risk

The committed fixtures are synthetic, so 1.00 is a simulated regression result,
not evidence that arbitrary real-estate naming conventions achieve 1.00. The
default-off runtime flag and explicit measurement label preserve that boundary.
