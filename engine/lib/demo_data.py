#!/usr/bin/env python3
"""Demo-data reset — the Settings view's danger zone (and `make clear-demo`).

Deletes everything the pipeline *generated* (run history + archived diffs,
review/queue/webhook state, test plans, test data, exports, logs, scratch
dirs, CI-health ingest, the SQLite index) while keeping everything the estate
*is* (registry, catalog JSONL, AGENTS.md, demo repos, prompts). After a clear,
`make demo-bootstrap` / `make demo-pr` rebuild the demo state from scratch.

Refuses to run while a pipeline run holds out/.pipeline.lock.
"""
import pathlib, shutil, sys

ROOT = pathlib.Path(__file__).resolve().parents[2]

# Directories whose CONTENTS are demo output (dir is recreated empty) and
# generated single files. reports/*.log run artifacts are globbed separately.
CLEAR_DIRS = ["reports/runs", "reports/exports", "reports/inline",
              "out", "workspace", "testplans", "testdata"]
CLEAR_FILES = ["reports/dashboard.html", "reports/catalog.db", "catalog/health.json"]


def _files_under(p):
    return [q for q in p.rglob("*") if q.is_file()] if p.is_dir() else []


def clear(root=None, dry=False):
    """Delete generated demo data under `root`. Returns {"removed": n,
    "targets": [relative paths]}; dry=True only reports."""
    root = pathlib.Path(root or ROOT)
    if (root / "out/.pipeline.lock").exists():
        raise SystemExit("refusing to clear: a pipeline run is in progress "
                         "(out/.pipeline.lock exists)")
    removed, targets = 0, []
    for rel in CLEAR_DIRS:
        d = root / rel
        files = _files_under(d)
        if not d.exists():
            continue
        removed += len(files)
        targets.append(rel + "/")
        if not dry:
            shutil.rmtree(d, ignore_errors=True)
            d.mkdir(parents=True, exist_ok=True)
    for f in list((root / "reports").glob("*.log")) + [root / p for p in CLEAR_FILES]:
        if f.exists():
            removed += 1
            targets.append(f.relative_to(root).as_posix())
            if not dry:
                f.unlink()
    return {"removed": removed, "targets": targets}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    dry = "--dry" in sys.argv
    r = clear(dry=dry)
    verb = "would remove" if dry else "removed"
    print(f"{verb} {r['removed']} generated file(s):")
    for t in r["targets"]:
        print(f"  {t}")
    if not dry:
        print("estate kept: registry, catalog, AGENTS.md, demo repos. "
              "Rebuild demo state with: make demo-bootstrap && make demo-pr")
