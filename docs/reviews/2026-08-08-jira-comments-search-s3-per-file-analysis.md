# JCTS-S3 per-file analysis

Date: 2026-08-08
Scope: unconditional ticket-comment outcome accounting.

| File | Responsibility reviewed | Findings and disposition |
| --- | --- | --- |
| `engine/lib/ticket_comment.py` | Pure receipt schema, Tracker invocation, scratch/event/state persistence, failure sanitization | Fixed P1: raw adapter stdout/stderr is never persisted; only bounded exit/HTTP/class metadata survives. Calls and persistence are total, bodies are excluded, identifiers and outcomes are closed, writes are locked, corrupt rows are counted. |
| `engine/pipeline.sh` | All existing ticket-comment call sites, scratch lifecycle, refusal ordering | Fixed P1: the reviewer-refusal run record was formerly assembled before its delivery attempt; it now follows the attempt. Routing, requirements, clarification, plan, delivery, and budget abort use the one boundary; no direct `TRACKER comment` remains. Scratch is cleared per run. |
| `adapters/tracker/jira.sh` | Jira post response contract | Returns the server comment id while retaining the existing Tracker verb and ADF/plain-text-safe body path. Curl/JSON failure remains a nonzero adapter result. |
| `adapters/mock/tracker.sh` | Mock parity | Emits a synthetic comment id and retains the existing PII-free mock log behavior. |
| `engine/lib/run_record.py` | Durable delivery receipt home | Adds explicit `comments`, including empty for new records, filters by run id, and records malformed receipt count without risking the rest of the record. |
| `engine/lib/plan_state.py` | No-run plan/requirements provenance and dashboard linking action | Stores normalized receipts with a 100-entry bound. The existing link action now uses the shared boundary but preserves its explicit UI failure and `commented` marker contracts. |
| `engine/lib/event_log.py` | Closed activity vocabulary | Adds `ticket.comment`; existing event redaction and never-raise guarantees remain authoritative. |
| `engine/lib/run_progress.py` | Live/historical operator visibility | Exposes valid receipts, failures, and corrupt count; malformed legacy count values degrade to zero instead of breaking the endpoint/CLI. |
| `engine/lib/explain.py` | Evidence-backed requester-notification explanation | Adds a notification decision from recorded receipts and an explicit unexplained row when history is incomplete; makes no inference from missing pre-S3 data. |
| `bin/dashboard.py` | Run-progress failure presentation | Escapes target/detail and renders failed delivery plus corrupt-history cards without changing run verdict or retry authority. |
| `docs/user-guide.md`, `docs/architecture.md` | Operator and architecture contract | Document stores, best-effort semantics, Tracker boundary, payload exclusion, and visibility. |
| S3 regression/adjacent tests | Unit, adapter, lifecycle, UI and real mock journeys | Covers closed model, success/failure, ids, token/body exclusion, torn/legacy data, event/state/run homes, pipeline ordering, dashboard visibility, plan no-record invariant, and full JIRA delivery. |

No open P0-P2 defect remains in the selected file set.
