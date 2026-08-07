# B2 Bounded Repair — Cross-File Integration Review

Date: 2026-08-06
Scope: repair orchestration, reviewer evidence, metering, caching, delivery, and surfaces

## Correctness

- Initial approve/skipped/unavailable results make no repair call. Initial
  needs-work starts history at iteration zero and selects only repositories
  represented in unresolved findings.
- Each loop performs all affected per-repo repairs, one merged validation, and
  one full reviewer fan-out. Unique iteration labels preserve every contract
  and avoid spend-ledger collisions.
- An addressed finding is removed only after a physical edit is evidenced and
  rereview omits the same repo/category/file/test identity. Empty repairs and
  repeated findings survive, including across a raw approve verdict.
- Run-record review surfaces consume the same normalized history; gate-derived
  `overall` and human review state remain independent.

## Security

- Repair sees ticket, findings, source, conventions, and catalog text only as
  untrusted data. It has no Bash/Write tools, cannot run tests, and is instructed
  not to touch application source, mappings, configuration, or git.
- Contract normalization accepts only existing generated paths under the target
  test repo, rejects traversal/absolute/duplicate/new-file evidence, and binds
  the declared fix set to actual before/after edits.
- Nested durable evidence is treated as hostile on every load. Malformed or
  oversized history cannot reach comments, explain, or dashboards as trusted.
- The agentic phase is impossible on completion-only providers and is denied
  from local and durable reuse, preventing contract replay without file effects.

## Reliability and deployment

- Repair, validation, and rereview all cross the budget guard; exit 77 is the
  hard backstop. `review.max_loops` is separately normalized and capped at 100,
  with the shipped default of one.
- Once mutation starts, any phase, contract, or validation failure exits before
  the gate. Initial read-only reviewer outages remain total/advisory as in B1.
- JSON history and contract writes use the shared Windows-retrying atomic
  writer. Existing feature flags and default-off behavior are unchanged; no
  migration, dependency, adapter, container, or gate change is required.
- Real-model repair quality and spend are not claimed by the mock path. The mock
  proves orchestration and evidence plumbing only.

## Test coverage

- Unit/adversarial coverage includes cap normalization, target selection, path
  escape, duplicate indexes/files, source/edit mismatch, strict booleans,
  apply-time tampering, no-op laundering, repeated findings, nested-history
  tampering, run-record persistence, and cache denial.
- The functional mock pipeline exercises a real two-repo needs-work run and
  proves exactly one repair/validate/rereview iteration with attributable
  contracts.
- Surface and compatibility tests cover comments, explain, progress, task
  bundles, phase inventory, providers, caches, and the full registry.

## Outcome

All actionable P0–P2 findings were fixed. B2 is release-ready behind the
existing reviewer flag. B3 remains the next backlog item and solely owns
delivery refusal under `require`.
