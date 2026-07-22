#!/usr/bin/env python3
"""Demo-data reset — the Settings view's danger zone (and `make clear-demo`).

Deletes everything the pipeline *generated* (run history + archived diffs,
review/queue/webhook state, test plans, test data, exports, logs, scratch
dirs, CI-health ingest, the SQLite index) while keeping everything the estate
*is* (registry, catalog JSONL, AGENTS.md, demo repos, prompts). After a clear,
`make demo-bootstrap` / `make demo-pr` rebuild the demo state from scratch.

Refuses to run while a pipeline run holds out/.pipeline.lock.
"""
import contextlib, os, pathlib, shutil, stat, sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import fs_lock


def _rmtree(d):
    """rmtree that also removes read-only files (git objects are r--r--r-- on
    Windows; plain ignore_errors would silently leave the clone behind)."""
    def _onexc(fn, path, exc):
        os.chmod(path, stat.S_IWRITE)
        fn(path)
    shutil.rmtree(d, onexc=_onexc)

# Directories whose CONTENTS are demo output (dir is recreated empty) and
# generated single files. reports/*.log run artifacts are globbed separately.
CLEAR_DIRS = ["reports/runs", "reports/exports", "reports/inline",
              "out", "workspace", "testplans", "testdata"]
CLEAR_FILES = ["reports/dashboard.html", "reports/catalog.db", "catalog/health.json"]


def _files_under(p):
    # our own advisory-lock dirs (<state>.lock/owner) are not user data — skip
    return [q for q in p.rglob("*") if q.is_file()
            and not q.parent.name.endswith(".lock")] if p.is_dir() else []


def clear(root=None, dry=False):
    """Delete generated demo data under `root`. Returns {"removed": n,
    "targets": [relative paths]}; dry=True only reports."""
    root = pathlib.Path(root or ROOT)
    if (root / "out/.pipeline.lock").exists():
        raise SystemExit("refusing to clear: a pipeline run is in progress "
                         "(out/.pipeline.lock exists)")
    removed, targets = 0, []
    # Hold the state-file locks while wiping reports/runs so a queue worker or the
    # hook server can't interleave a save() and resurrect half-deleted state.
    with contextlib.ExitStack() as locks:
        if not dry:
            for name in ("queue.json", "reviews.json", "hooks-seen.json"):
                locks.enter_context(fs_lock.lock(root / "reports/runs" / name))
        for rel in CLEAR_DIRS:
            d = root / rel
            files = _files_under(d)
            if not d.exists():
                continue
            removed += len(files)
            targets.append(rel + "/")
            if not dry:
                _rmtree(d)
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
