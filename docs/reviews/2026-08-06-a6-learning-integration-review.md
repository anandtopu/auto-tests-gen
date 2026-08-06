# A6 Learning Loop — Cross-file Integration Review

Date: 2026-08-06

## Correctness and lifecycle

The gate emits `GATE_STATUS=COMMITTED` only after commit and push succeed. A6
consumes those rows after the parallel gate loop, resolves full SHAs, reads
committed file bytes, upserts only changed spec paths, refreshes configured
vectors, and writes its result before `run_record.py`. Non-committed statuses do
nothing. Retry uses a stable run/repo/SHA event identity and remains idempotent.

Review approval/changes-requested transitions and A4 duplicate exclusions append
events linked to the latest produced run's case/chunk IDs. The chunk store is a
code-derived cache; durable human provenance lives under `reports/runs/`, so full
state export carries it while the knowledge-only profile correctly excludes team
review history.

## Security and reliability

- A6 never invokes `git commit` or `git push`; the gate's authority is unchanged.
- Repository names must be registered, Git is invoked with argument arrays, and
  file bytes come from an established commit rather than the working tree.
- Hostile test bodies pass through the existing parser/data framing and cannot
  affect tool or gate authority.
- `fs_lock` plus atomic replacement prevents lost concurrent events. Malformed
  provenance blocks append without overwriting forensic bytes.
- Index/vector failure is visible as unavailable and cannot reclassify an already
  pushed test commit as failed or quarantined.

## Ranking and coverage

Outcome ranking is off unless `AIQE_ARTIFACT_REUSE=1`. Even when enabled it does
not modify confidence, similarity, threshold, recommendation, or candidate
membership; it only orders equal-confidence rows before existing health/recency
tie-breakers. Strong deterministic surface evidence always remains first.

Focused tests cover real temporary Git commits, short-to-full SHA resolution,
same-run A3 retrieval, replacement/deletion semantics, no-change/failure gates,
unparsed visibility, retry idempotence, concurrent events, torn provenance,
latest-review semantics, duplicate linkage, review immutability, and pipeline
ordering. Broad validation remains the release gate.

## Validation

- Targeted A6/impact/retrieval/review/selection/run-record/state-bundle suite:
  85 passed.
- Full registry compatibility suite with mock adapters: 1,349 passed in 611.01s.
- Ruff on new/changed A6 source and tests: passed.
- `bash -n engine/pipeline.sh`: passed through the configured Git Bash bridge.

## Residual risk

Reviewer edit diffs remain explicitly out of A6 scope (PRD D6); A6 learns from
commit and decision outcomes, not the content of subsequent human edits.
