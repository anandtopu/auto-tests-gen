# JIRA comments/search PRD — per-file analysis

## Scope

Pass 1 reviewed the new PRD against the current implementation surfaces before
changing behavior. The generated `AGENTS.md` timestamp is unrelated and excluded.

### `docs/prd-jira-comments-and-ticket-search.md`

- **Purpose:** normative requirements for richer ticket feedback and structured discovery.
- **Findings:** v2 resolves the two otherwise blocking persistence questions:
  plan-mode receipts live in `plan_state`/events, and comment ids have explicit
  homes. Its S1–S5 order is dependency-coherent and correctly elevates G6.
- **Risk:** Q1/Q2/Q4 remain product choices but none blocks S1–S3 or the required
  plain-text floor. Saved presets are explicitly out of current scope.

### `adapters/tracker/jira.sh`

- **Purpose:** live Tracker REST boundary.
- **Finding JCTS-01 (P1):** `search_release` interpolates untrusted release text
  directly inside quoted JQL. A quote escapes the literal and changes query logic.
- **Required change:** compose only closed structured filters inside the adapter,
  escape every literal, expose returned/total, and keep HTTP failures truthful.

### `adapters/mock/tracker.sh`

- **Purpose:** credential-free Tracker behavior for eval and UI development.
- **Finding JCTS-02 (P1):** only release filtering exists, so a UI could not ship
  all six filters without bypassing port conformance or requiring credentials.
- **Required change:** consume the identical closed filter contract, AND filters,
  and return the same envelope and ticket projection as Jira.

### `adapters/conformance/test_adapters.sh`

- **Purpose:** adapter verb surface pin.
- **Finding:** Tracker conformance does not require `search`; extending the port
  without this pin would permit a partial deployment.

### `engine/lib/ticket_fields.py`

- **Purpose:** one parsing authority for ticket attributes used at runtime.
- **Finding JCTS-03 (P2):** processed-field names exist only implicitly in code;
  copying them into search would permit discovery/runtime vocabulary drift.
- **Required change:** export the four shared names; search adds only status/text.

### `bin/dashboard_server.py`

- **Purpose:** Intake API and adapter process boundary.
- **Finding:** `jira_items(release)` assumes list output and only passes release.
  It must remain unchanged in S1 to protect compatibility, then migrate under the
  S2 flag to the structured envelope with a distinct failure response.

### `bin/dashboard.py`

- **Purpose:** Intake and queue UI.
- **Finding JCTS-04 (P2):** current release-only controls, no population count,
  and no bulk confirmation cannot satisfy B2 safely. This is correctly deferred
  until the S1 contract is conformance-tested.

### `engine/lib/work_queue.py`

- **Purpose:** durable manual queue and runtime handoff.
- **Finding:** queue records lack discovery attributes. Adding them is compatible
  only if reads remain defensive and `run_all()` never promotes them to runtime
  authority. S2 tests must pin both properties.

### Comment call sites in `engine/pipeline.sh` and `engine/lib/plan_state.py`

- **Purpose:** five current requester-notification sites.
- **Finding JCTS-05 (P1):** best-effort `|| true` makes delivery failure and retry
  duplication unobservable. The PRD correctly separates unconditional accounting
  (S3) from richer content (S4) and idempotency (S5).

### `engine/lib/pr_comment.py`, `spec_store.py`, `run_record.py`, `run_progress.py`, `explain.py`

- **Purpose:** existing rich projections and the intended status consumers.
- **Finding:** these supply the correct reuse seams; a new independent delivery
  formatter would violate M5. S3 must add schema/read compatibility before S4
  reuses the projection and S5 stores ids.

## Pass-1 conclusion

Implementation can proceed in PRD slice order. S1 is the only safe first item:
it fixes an exploitable query-construction defect before widening the search UI.
