# Data durability and portability

How platform state survives restarts, redeploys and moves between deployments, and
what is deliberately not carried.

## 1. What already survives what

| Layer | Restart (pod recreated) | Redeploy (new image) | New deployment (new PVC / cluster / machine) |
|---|---|---|---|
| `registry/`, `knowledge/repos`, `knowledge/curated`, `catalog/*.jsonl` | ✅ in the image and in git | ✅ | ✅ if the repo is the same commit |
| `reports/` — run records, diffs, plans, review board, OpenHands trace | ✅ PVC (`ai-qe-reports`) | ✅ PVC outlives the pod | ❌ **starts empty** |
| `out/`, `workspace/` | ❌ `emptyDir`, by design | ❌ | ❌ |
| `reports/phase-cache/`, `reports/catalog.db`, `knowledge/generated/` | ✅/❌ irrelevant | — | ❌ regenerable |

So restarts and redeploys were already safe: `deploy/openshift/deployment.yaml` mounts
`reports/` from a ReadWriteOnce PVC and only mounts `out/`/`workspace/` as `emptyDir`.

The gap was the last column. A new namespace, a rebuilt cluster, a second environment
or a laptop-to-server move started blank, and there was no way to carry the work over.

## 2. The state bundle

```bash
make state-export                                  # -> reports/exports/<stamp>-state.tar.gz
make state-inspect BUNDLE=reports/exports/x.tar.gz # manifest + checksum check, no writes
make state-import  BUNDLE=x.tar.gz                 # merge into this deployment
make state-import  BUNDLE=x.tar.gz REPLACE=1       # overwrite local copies
make state-import  BUNDLE=x.tar.gz DRY=1           # show what would change
```

### Carried — the state that *is* somebody's work

`registry/repo-registry.yaml`, `AGENTS.md`,
`knowledge/repos/` + `knowledge/curated/` + `knowledge/synced/`, `catalog/*.jsonl` +
`catalog/review/`, `reports/runs/` (records, archived diffs, `reviews.json`, and
append-only `testcase-provenance.jsonl` learning outcomes),
`reports/plans/`, `reports/openhands/state.json`, `testplans/`, `testdata/`.
Full state also carries `reports/agent-artifacts/`: immutable B1 blobs, references,
and B2 task manifests needed to explain historical runs. Export and import hold the
artifact-store mutation lock, and import relocates these members through
`AIQE_ARTIFACTS_DIR`/`AIQE_STATE_DIR` rather than assuming the checkout path.
`registry/org-config.yaml` travels as policy evidence (and remains part of the
knowledge-only profile), but import always preserves the receiving image's copy.

### Not carried, and why

| Excluded | Reason |
|---|---|
| `.env`, `aiqe.properties` | **Credentials.** A bundle gets emailed, copied between machines and attached to tickets. Secrets must never be in one. Configure them per deployment. |
| every `.py` / `.sh` | Code ships in the image. Excluding by *directory* missed `catalog/review/export_review_queue.py`, so the rule is by suffix — the next script dropped next to a data file is covered too. An import must never overwrite live tooling with an older revision. |
| `catalog/schema.json`, `specs/platform/` | Image-owned schema and platform constitution. Older schema-1 bundles are accepted, but these members are preserved rather than restored. |
| `out/`, `workspace/` | Per-run scratch. Carrying it moves stale derived data around. |
| `reports/phase-cache/` | Content-addressed; rebuilds itself, and its keys are local. |
| `reports/catalog.db` | Rebuilt by `make catalog-db` from the JSONL that *is* carried. |
| `reports/runs/queue.json` | In-flight work, not decisions — see the `.gitignore` note. |
| `knowledge/generated/` | Regenerated on demand from the registry and harvested surface. |
| `reports/exports/`, `dashboard.html`, `*.log` | Outputs, not state. |
| `reports/agent-artifacts/` in the knowledge-only profile | Run-scoped audit history is not shared as reusable team knowledge. The full state profile carries it. |

### Integrity, and why the manifest matters

`manifest.json` records the schema version, origin host, timestamp and a **sha256 per
file**. `state-inspect` recomputes every checksum and returns non-zero for a missing,
extra, duplicate, malformed, unsafe or mismatched member. A bundle is untrusted
input — it arrives over email or a shared drive — so the import preflights the whole
archive before creating a destination or acquiring a mutation lock, then:

- **rejects** any file whose checksum does not match, and writes none of the bundle;
- **rejects** members outside the export allowlist even when their self-supplied
  checksum is correct, so a bundle cannot overwrite application code;
- **aborts** on POSIX or Windows path traversal (`../` and `..\\`) and duplicate,
  undeclared, missing or non-file state members;
- **refuses** to run while `out/.pipeline.lock` is held, because rewriting state under
  a live run is how you get a half-imported estate (`--force` overrides a stale lock).

### merge vs replace

`merge` (default) writes only paths that are **absent** locally, so a populated
deployment keeps everything it has and a second merge is a no-op. `--replace`
overwrites every mutable bundled path. Image-owned policy/schema members are always
preserved. Both report counts; `--dry-run` writes nothing, including no lock parent.

After a non-trivial import, regenerate derived data — the CLI says so:

```bash
make agents && make catalog-db
```

## 3. OpenHands request traceability

Every request the platform sends to OpenHands is now recorded **before** the call, so
the trace does not depend on the call succeeding:

1. `openhands_events.record_request()` writes a row keyed by a local request id
   (uuid-suffixed — a timestamp+pid pair collided for two requests in the same second
   and the second silently overwrote the first).
2. On success `resolve_request()` **re-keys** the row to the conversation id, so the
   webhook stream — which only knows conversation ids — enriches the same row instead
   of creating a second one.
3. On failure the row stays under its request id with the error text. A 502
   (unreachable, credentials rejected, both conversation endpoints refused) used to
   answer the user and leave nothing behind, which is exactly the case someone needs
   to investigate.

View it with `bin/qa.py openhands`, `GET /api/openhands`, or the conversations card in
the Runs and Test plans views. The state file is carried by the bundle, so the trace
moves with the deployment.

## 4. PR → E2E generation context

The generate phase is told to *"update existing tests listed in the contract before
creating new ones"* and to *"reuse page objects / service clients; extend, never
duplicate."* It was given `out/triage.contract.json`, whose `existing_tests` is a list
of test-id **strings** — not the catalog slice with file paths, titles, layers and
app-repo mappings. It was being asked to extend tests it could not see.

`out/catalog-slice.jsonl` now reaches **all three** generate call sites (PR,
JIRA single-pass, and resume-from-approved-plan), alongside `AGENTS.md`, the phase
contracts, the PR diff, coverage gaps and `out/repo-conventions.md`. A test pins all
three call sites so a fourth path cannot be added without it.

Worth stating honestly: this closes a real context gap, and the scorecard's
`update-vs-create: 0%` is *consistent* with the gap — but that figure comes from mock
runs, where `mock_phase.sh` hardcodes `"action":"created"`. Whether real generation now
extends more and duplicates less can only be measured by a real-LLM run
(REVIEW.md open item 5).

## Retrieval substrate (cost-reduction stack)

`reports/knowledge-index/` (chunks.jsonl + vectors.db + embed-spend.json) is
DERIVED data: excluded from every bundle profile, removed by clear-demo, and
rebuilt on the receiving deployment with `make index-rebuild` (the import
command's "Next:" line says so). `reports/cost-baseline.json` is deliberately
local too — a baseline describes ONE deployment's measured costs and must be
re-frozen (`make cost-baseline`) where the runs actually happen.
