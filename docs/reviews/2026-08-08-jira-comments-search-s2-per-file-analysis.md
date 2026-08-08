# JCTS-S2 per-file analysis

Date: 2026-08-08
Scope: intake filters, result rendering, bulk queue handoff, and queue provenance.

## Production and configuration files

| File | Review | Findings and disposition |
| --- | --- | --- |
| `bin/dashboard_server.py` | Query trust boundary, adapter envelope validation, legacy response compatibility, error mapping | Fixed P1: malformed adapter rows and dishonest counts are rejected before reaching the browser. Unknown, repeated, ambiguous, and raw-JQL-shaped parameters return 400; adapter/runtime failures return 502 and cannot masquerade as an empty result. Default-off requests retain the old list response. |
| `bin/dashboard.py` | Flagged controls, result attributes/counts, bulk scope, failure semantics, queue projection | Fixed P2: synchronized UI schema with the server at 3. The bulk loop uses only the returned page and calls the single-item endpoint for every row after the exact N-of-M confirmation. A failed request clears stale results and is visibly distinct from a valid zero-row page. Partial failure is explicit rather than claiming atomicity. |
| `engine/lib/work_queue.py` | Stored metadata types/bounds, dedupe ordering, compatibility, execution boundary | Fixed P1: validate provenance before the dedupe return so duplicate submissions cannot bypass validation. Metadata is optional and omitted when empty, preserving default-off/legacy record shape. `run_all` does not pass it to pipeline execution. |
| `engine/lib/settings_store.py` | Settings exposure and default | Added boolean-compatible `AIQE_TICKET_SEARCH` setting with default `0`; no unrelated settings changed. |
| `.env.example`, `aiqe.properties.example` | Deployment default and operator discoverability | Both explicitly default the feature off. No credentials or environment-specific values were introduced. |
| `docs/user-guide.md` | Operator contract and authority boundary | Documents structured filters, N-of-M semantics, sequential per-item queueing, display-only provenance, runtime refetch, and failure versus empty behavior. |

## Tests

| File | Review | Coverage |
| --- | --- | --- |
| `registry/tests/test_ticket_search_ui.py` | Focus, determinism, adversarial inputs, boundary pinning | Covers HTTP filter mapping/rejection, malformed adapter responses, truthful totals, metadata bounds and legacy reads, validation-before-dedupe, runtime refetch authority, flag-on/off HTML, exact confirmation/failure text, JavaScript parsing, and a live mock-server search-to-queue journey. Synthetic values contain no PII or credentials. |

## Outcome

No open P0-P2 finding remains in the S2 file set. The changes preserve the
default-off API/record shape and constrain all new external input before storage
or rendering.
