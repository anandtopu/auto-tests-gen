# Exploratory E2E Review — Iteration 013

## Scope

This iteration completed Feature 12, Test catalog, through the supported CLI
and served browser UI. It covered search and mapping filters, valid/invalid
human mapping decisions, coverage regeneration, CI result ingest, flaky health,
orphan mapping, quarantine visibility, concurrent writers and interrupted
catalog persistence.

## Findings

| ID | Severity | Files | Finding | Impact | Fix |
| --- | --- | --- | --- | --- | --- |
| E2E-EXP-028 | P2 | bin/qa.py | An empty repository decision became confirmed with confidence 1.0. | Overview counted an unmapped row as mapped and operators could not distinguish it from reviewed evidence. | Reject empty/delimiter-only decisions before mutation and direct users to explicit ORPHAN. |
| E2E-EXP-029 | P1 | bin/qa.py | Catalog shards were rewritten directly and read before any writer lock. | A crash could truncate the source of truth; concurrent human decisions could overwrite one another. | Serialize first, atomically replace, and lock the complete read-modify-replace transaction. |
| E2E-EXP-030 | P2 | bin/dashboard.py | Catalog rows ignored the quarantine tag and note. | A known flaky quarantined test looked like an ordinary auto/confirmed mapping. | Render an escaped quarantine chip and note beside mapping status. |
| Review-013-A | P3 | registry/tests/test_ticket_discovery.py | A regression asserted retired direct subprocess argv. | The full suite failed only on Windows even though the normalized runner contract was correct. | Assert semantic script/args before the Git-Bash wrapper. |
| Review-013-B | P2 | registry/tests/test_multi_agent.py | Adversary lifecycle tests reused shared phase-cache state. | Full-suite order could return an unrelated authored contract and make the check nondeterministic. | Disable phase caching for the two tests that require fresh adversary output. |

## Reproduction and retest evidence

- Before E2E-EXP-028, `map <orphan-id> --repos ''` exited 0 and persisted
  `app_repos=[]`, `status=confirmed`, `confidence=1.0`. Catalog showed
  `✓ confirmed` with app repo `—`. Afterward it exits 1, names ORPHAN,
  and the shard hash is unchanged.
- Before E2E-EXP-029, a simulated serialization interruption after the first
  row changed a two-row shard into one partial new row. Afterward the original
  bytes remain intact and no temp file survives. Two simultaneous real
  quarantine CLI processes also preserved both independent notes.
- Three matching synthetic Jenkins cases plus one unmatched case produced three
  runs, one failure, 67% pass and FLAKY. The CLI identified the row and
  quarantine proposal; the served Catalog rendered the same health.
- Before E2E-EXP-030, reloading after quarantine showed only `✓ auto` and
  FLAKY. Afterward the same row visibly contains `⚠ quarantined` and the
  escaped human note. Repo, status, search and empty-result filters retained
  the correct 1/4, 1/4, 1/4 and 0/4 counts.

## Pass 1 — per-file review

- `bin/qa.py`: empty decisions fail before mutation. JSONL payloads are
  completely serialized before a same-directory temp write and atomic replace.
  Map, review and quarantine share the shard lock across load, mutation and
  replace. Unknown ids and repos retain actionable failures.
- `bin/dashboard.py`: quarantine remains orthogonal to mapping status,
  appears beside it, and escapes the operator note before HTML insertion.
- Catalog tests pin empty-decision immutability, interrupted-save preservation,
  temp cleanup, quarantine persistence/lift, health output and UI rendering.
- Broad-suite test corrections assert supported contracts without weakening
  queue normalization or adversarial-plan behavior.

## Pass 2 — cross-file review

- Correctness: UI and CLI agree on four catalog rows, orphan/mapped tiers, CI
  health and quarantine. Coverage regeneration follows the isolated registry.
- Security: operator quarantine notes are HTML-escaped; synthetic fixtures
  contain no credentials or PII; unknown application repos remain rejected.
- Reliability: catalog readers see either the complete old shard or complete
  new shard. Concurrent human decisions are serialized and test isolation no
  longer depends on ambient phase cache.
- Deployment: no schema, dependency, migration, manifest or external service
  changed. Existing JSONL shards and optional quarantine fields remain
  backward-compatible.
- Coverage: 18 focused, 97 adjacent, 12 queue and 35 multi-agent checks passed.
  Two broad runs passed 1,627/1,628 while independently exposing the two
  suite-isolation findings above; each failed contract passes after correction.

## Seed and cleanup review

All mutable catalog, registry, AGENTS, query-index, health and Jenkins-result
state lived under ignored `out/exploratory-e2e-iter13`. The local server
bound only to 127.0.0.1 with a test token. The seed used synthetic product names
already present in the demo estate and no production adapter.

## Residual risk

- The complete 1,628-test suite was not run a third time after the second
  unrelated isolation correction; both full attempts passed the other 1,627
  tests, and both corrected test files pass completely.
- Quarantine remains a proposal/tag by design; the platform does not edit a
  test repository's CI configuration.
- No blocker remains for Feature 12. Feature 13, Repositories, is next.
