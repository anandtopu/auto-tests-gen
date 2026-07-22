#!/usr/bin/env python3
"""Demo-data reset — the Settings view's danger zone (and `make clear-demo`).

Deletes everything the pipeline *generated* (run history + archived diffs,
review/queue/webhook state, test plans, test data, exports, logs, scratch
dirs, CI-health ingest, the SQLite index) while keeping everything the estate
*is* (registry, catalog JSONL, AGENTS.md, demo repos, prompts). After a clear,
`make demo-bootstrap` / `make demo-pr` rebuild the demo state from scratch.

Refuses to run while a pipeline run holds out/.pipeline.lock.
"""
import contextlib, os, pathlib, shutil, stat, sys, time

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


# Matches the stale-lock threshold in engine/pipeline.sh: a lock older than this
# belonged to a run that was killed or crashed, not one still working.
STALE_LOCK_MINUTES = 90


def _check_pipeline_lock(root, dry, force):
    """Refuse only while a run is plausibly LIVE. A killed run leaves the lock dir
    behind forever, and refusing on that made 'Clear demo data' fail permanently
    with a message that was both untrue and unactionable."""
    lock = root / "out/.pipeline.lock"
    if not lock.exists():
        return None
    age_min = (time.time() - lock.stat().st_mtime) / 60
    if age_min > STALE_LOCK_MINUTES:
        if not dry:
            shutil.rmtree(lock, ignore_errors=True)
        return f"out/.pipeline.lock (stale, {age_min:.0f} min old — removed)"
    if force:
        if not dry:
            shutil.rmtree(lock, ignore_errors=True)
        return f"out/.pipeline.lock ({age_min:.0f} min old — force-removed)"
    raise SystemExit(
        f"refusing to clear: a pipeline run looks active "
        f"(out/.pipeline.lock, {age_min:.0f} min old). Wait for it to finish, or "
        f"clear anyway if you know it is dead (force).")


def clear(root=None, dry=False, force=False):
    """Delete generated demo data under `root`. Returns {"removed": n,
    "targets": [relative paths]}; dry=True only reports. `force` clears past a
    pipeline lock that is younger than the stale threshold."""
    root = pathlib.Path(root or ROOT)
    removed, targets = 0, []
    note = _check_pipeline_lock(root, dry, force)
    if note:
        targets.append(note)
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
    r = clear(dry=dry, force="--force" in sys.argv)
    verb = "would remove" if dry else "removed"
    print(f"{verb} {r['removed']} generated file(s):")
    for t in r["targets"]:
        print(f"  {t}")
    if not dry:
        print("estate kept: registry, catalog, AGENTS.md, demo repos. "
              "Rebuild demo state with: make demo-bootstrap && make demo-pr")
