#!/usr/bin/env python3
"""Demo-data reset — the Settings view's danger zone (and `make clear-demo`).

Deletes everything the pipeline *generated* (run history + archived diffs,
review/queue/webhook state, plan state + contract snapshots, OpenHands event
ingest, test plans, test data, exports, logs, scratch dirs, CI-health ingest,
the SQLite index, the bootstrapped catalog JSONL + review queues, and the
derived guidance caches under knowledge/generated and knowledge/synced) while
keeping everything the estate *is*: the registry (your repo CONFIGURATION),
catalog/bootstrap code, the catalog sample + schema, AGENTS.md, demo repos,
prompts, and knowledge/repos team notes. After a clear,
`make demo-bootstrap` / `make demo-pr` rebuild the demo state from scratch.

Refuses to run while a pipeline run holds out/.pipeline.lock.
"""
import argparse, contextlib, os, pathlib, shutil, stat, subprocess, sys, time

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import app_paths                      # R12: mutable paths resolve here
import fs_lock


# Stores whose location `app_paths.resolve_rel` does NOT own, because each keeps
# its own knob. Ask the owning module rather than re-deriving the rule here —
# the precedent is selection.py, which hardcoded `reports/plans/` while
# plan_state honoured AIQE_PLAN_DIR, and split one review across two directories.
def _plans_dir():
    import plan_state
    return plan_state.DIR


def _openhands_dir():
    import openhands_events
    return openhands_events.FILE.parent


def _catalog_db():
    v = (os.environ.get("AIQE_CATALOG_DB") or "").strip()
    return pathlib.Path(v) if v else app_paths.ROOT / "reports/catalog.db"


_OWNED = {
    "reports/plans": _plans_dir,
    "reports/openhands": _openhands_dir,
    "reports/catalog.db": _catalog_db,
    "catalog/health.json": lambda: app_paths.catalog_health(app_paths.ROOT),
}


def _target(root, rel):
    """Where `rel` ACTUALLY lives — asked of its owner, never assembled here.

    This is a DESTRUCTIVE module, and every path it joined by hand was a path it
    could delete in the wrong tree. `_state`'s own docstring states the rule —
    "a container that relocated state clears the copy actually in use rather
    than the pristine one baked into the image" — and it was applied to exactly
    one of fifteen entries. Measured with AIQE_STATE_DIR set against the real
    checkout: `clear()` reported 3139 files removed and NOT ONE of its targets
    named the relocated tree, while planted operator state under
    testplans/, testdata/, specs/, knowledge/ and reports/exports/ survived
    untouched. In the deployed shape that is the Settings view's Factory reset
    button reporting a file count for work it did not do — and reaching into
    `/app`, which is the read-only image.

    A caller-supplied root still wins, because tests pass a temp estate and
    must never be redirected onto the live one.
    """
    root = pathlib.Path(root)
    if root != app_paths.ROOT:
        return root / rel
    owner = _OWNED.get(rel)
    return owner() if owner else app_paths.resolve_rel(rel)



def _rmtree(d):
    """rmtree that also removes read-only files (git objects are r--r--r-- on
    Windows; plain ignore_errors would silently leave the clone behind).

    The handler ignores its third argument on purpose so ONE function serves both
    keyword spellings: `onexc` (3.12+) and `onerror` (3.11 — the container image's
    python). Passing onexc on 3.11 raised TypeError, which broke Clear demo data /
    Factory reset ONLY inside the container — the class of bug a host-side suite
    can never catch."""
    def _fix(fn, path, _exc):
        os.chmod(path, stat.S_IWRITE)
        fn(path)
    if sys.version_info >= (3, 12):
        shutil.rmtree(d, onexc=_fix)
    else:
        shutil.rmtree(d, onerror=_fix)

# Directories whose CONTENTS are demo output (dir is recreated empty) and
# generated single files. reports/*.log run artifacts are globbed separately.
#
# Every store the platform WRITES belongs here. State stores are deliberately
# scattered (plan state is kept out of reports/runs/ so the run-record glob skips
# it, OpenHands events sit in their own dir), and each one added since this module
# was written was a store this list silently missed — leaving a "cleared" estate
# carrying state from before the clear. The most damaging was reports/plans/: an
# approval survived while the plan it approved was deleted, so generation would run
# against a stale sign-off for a plan that no longer existed.
CLEAR_DIRS = ["reports/runs", "reports/costs", "reports/exports", "reports/inline",
              "reports/plans",            # plan-first state + contract snapshots
              "reports/openhands",        # agent conversation/event ingest
              "knowledge/generated",      # generated per-repo AGENTS.md
              "knowledge/synced",         # SCM guidance cache (re-pull: make sync-guidance)
              "reports/knowledge-index",  # retrieval chunks (derived; make agents rebuilds)
              "out", "workspace", "testplans", "testdata",
              "specs"]                  # SDD spec store: per-key specs are
                                        # generated demo output. specs/platform/
                                        # (the constitution) is hand-authored
                                        # platform SPEC, preserved via
                                        # KEEP_SUBDIRS below.

# Subdirectories a wholesale dir-clear must PRESERVE: hand-authored content
# living inside an otherwise-generated tree. A clear that deleted the platform
# constitution would leave a running deployment without its own spec until
# someone ran git checkout.
KEEP_SUBDIRS = {"specs": {"platform"}}
CLEAR_FILES = ["reports/dashboard.html", "reports/catalog.db", "catalog/health.json"]

# Generated files that live BESIDE committed code/fixtures, so we clear by glob and
# keep the exceptions rather than wiping the whole directory. The bootstrapped catalog
# is demo output — `make demo-bootstrap` fully regenerates it (`> catalog/<repo>.jsonl`)
# — so a "clear" that left it behind is why the Test catalog and Coverage views looked
# untouched. catalog/bootstrap/ (code), catalog.sample.jsonl and schema.json stay.
CLEAR_GLOBS = [
    ("catalog/*.jsonl", {"catalog.sample.jsonl"}),
    ("catalog/review/*.csv", set()),
]

# Note: knowledge/repos/ is deliberately absent — those are hand-authored team notes,
# part of what the estate *is*. The registry is likewise kept: it is CONFIGURATION
# (in a real deployment it holds real repos), not generated demo output, so a
# demo-reset click must never destroy it. Remove repos individually in the
# Repositories view. Only derived data and generated caches are cleared here.


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
    did = "would be removed" if dry else "removed"      # a dry run removes nothing
    if age_min > STALE_LOCK_MINUTES:
        if not dry:
            shutil.rmtree(lock, ignore_errors=True)
        return f"out/.pipeline.lock (stale, {age_min:.0f} min old — {did})"
    if force:
        if not dry:
            shutil.rmtree(lock, ignore_errors=True)
        return f"out/.pipeline.lock ({age_min:.0f} min old — force-{did})"
    raise SystemExit(
        f"refusing to clear: a pipeline run looks active "
        f"(out/.pipeline.lock, {age_min:.0f} min old). Wait for it to finish, or "
        f"clear anyway if you know it is dead (force).")


EMPTY_REGISTRY = """# Emptied by the Settings factory reset — add repositories in the
# Repositories view (or bin/repos.py add-app / add-test), or restore the demo estate
# with: git checkout -- registry/repo-registry.yaml
source_repositories: []
test_repositories: []
routing_hints:
  jira_component_map: {}
  jira_label_map: {}
"""


def clear(root=None, dry=False, force=False, factory=False):
    """Delete generated demo data under `root`. Returns {"removed": n,
    "targets": [relative paths]}; dry=True only reports. `force` clears past a
    pipeline lock that is younger than the stale threshold.

    `factory` additionally deletes what a plain clear deliberately KEEPS — the
    registered repositories (registry -> empty estate) and hand-authored per-repo
    notes — because "delete all the demo data" to an operator includes the repos
    they added through the UI. The demo estate is restorable from git."""
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
            # plan state lives outside reports/runs/ but is mutated the same way,
            # so it needs the same protection from an interleaved save()
            locks.enter_context(
                fs_lock.lock(_target(root, "reports/plans") / "state.json"))
        for rel in CLEAR_DIRS:
            d = _target(root, rel)
            keep = KEEP_SUBDIRS.get(rel, set())
            files = [f for f in _files_under(d)
                     if not (keep and f.relative_to(d).parts[0] in keep)]
            if not d.exists():
                continue
            removed += len(files)
            targets.append(rel + "/")
            if not dry:
                # Wipe CONTENTS but keep advisory `*.lock` dirs alive: this clear
                # is itself holding several of them (see the ExitStack above), and
                # a plain rmtree would delete the held locks mid-wipe — from that
                # moment any concurrent worker could acquire by plain mkdir and
                # interleave writes with the wipe. Stale survivors are broken by
                # fs_lock's own orphan/stale logic later.
                for child in sorted(d.iterdir()):
                    if child.is_dir() and child.name.endswith(".lock"):
                        continue
                    if child.is_dir() and child.name in KEEP_SUBDIRS.get(rel, ()):
                        continue          # hand-authored (e.g. specs/platform)
                    if child.is_symlink():
                        # is_dir() follows links; rmtree refuses symlinks — the
                        # link itself is the thing to remove.
                        child.unlink()
                        continue
                    if child.is_dir():
                        _rmtree(child)
                    else:
                        try:
                            child.unlink()
                        except PermissionError:
                            os.chmod(child, stat.S_IWRITE)
                            child.unlink()
        logs = [("reports/" + f.name, f)
                for f in sorted(_target(root, "reports").glob("*.log"))]
        for label, f in logs + [(p, _target(root, p)) for p in CLEAR_FILES]:
            if f.exists():
                removed += 1
                targets.append(label)
                if not dry:
                    f.unlink()
        # Generated files interleaved with committed ones (the bootstrapped catalog):
        # clear by glob, keeping the named exceptions.
        for pattern, keep in CLEAR_GLOBS:
            base, _, leaf = pattern.rpartition("/")
            for f in sorted(_target(root, base).glob(leaf)):
                if not f.is_file() or f.name in keep:
                    continue
                removed += 1
                targets.append(f"{base}/{f.name}")
                if not dry:
                    f.unlink()
        if factory:
            reg = _target(root, "registry/repo-registry.yaml")
            if reg.exists():
                removed += 1
                targets.append("registry/repo-registry.yaml (emptied — all repositories removed)")
                if not dry:
                    reg.write_text(EMPTY_REGISTRY, encoding="utf-8", newline="\n")
            notes = _target(root, "knowledge/repos")
            # README.md is documentation, not a team note — it survives factory
            for f in (sorted(x for x in notes.glob("*.md")
                             if x.name.lower() != "readme.md")
                      if notes.is_dir() else []):
                removed += 1
                targets.append(f"knowledge/repos/{f.name}")
                if not dry:
                    f.unlink()
            # Curated guidance is user content tied to the (now removed) repos —
            # a factory reset deletes it; a plain clear deliberately KEEPS it.
            curated = _target(root, "knowledge/curated")
            for d in sorted(curated.iterdir()) if curated.is_dir() else []:
                if not d.is_dir():
                    continue
                removed += len(_files_under(d))
                targets.append(f"knowledge/curated/{d.name}/")
                if not dry:
                    _rmtree(d)
    if not dry:
        # `covers:` is GENERATED state (catalog evidence ∪ scope) and the evidence
        # was just deleted — without regenerating, the registry keeps stale
        # coverage and the Repositories & mapping page still shows repos as
        # covered/mapped after a clear. Same registry lock as every other caller.
        regen = root / "catalog/bootstrap/regen_coverage.py"
        reg_p = _target(root, "registry/repo-registry.yaml")
        if regen.exists() and reg_p.exists():
            with fs_lock.lock(reg_p):
                subprocess.run([sys.executable, str(regen)], cwd=root,
                               capture_output=True, stdin=subprocess.DEVNULL)
        # The estate knowledge and path skills are DERIVED from the registry and
        # the (now empty) catalog — regenerate on EVERY clear, not only factory,
        # or AGENTS.md keeps feeding phases coverage that no longer exists.
        for script in ("bin/gen_agents_md.py", "bin/gen_path_skills.py"):
            s = root / script
            if s.exists():                     # absent under a test's tmp root
                subprocess.run([sys.executable, str(s)], cwd=root,
                               capture_output=True, stdin=subprocess.DEVNULL)
    return {"removed": removed, "targets": targets, "factory": factory}


def main(argv=None):
    """CLI entry point. Parse every option before calling the destructive action."""
    import json as _json
    parser = argparse.ArgumentParser(
        description="Clear generated AI-QE demo data while preserving estate configuration.")
    parser.add_argument("--dry", action="store_true",
                        help="preview the generated files that would be removed")
    parser.add_argument("--force", action="store_true",
                        help="clear even when a recent pipeline lock exists")
    parser.add_argument("--factory", action="store_true",
                        help="also remove registered repositories and curated guidance")
    parser.add_argument("--json", action="store_true",
                        help="emit the machine-readable dashboard response")
    args = parser.parse_args(argv)
    if args.json:
        # Machine mode for the dashboard server, which runs this as a SUBPROCESS so a
        # long-lived server always executes the current clear targets — an in-process
        # `import demo_data` froze the list at server start, and a server started
        # before a fix kept clearing the old, incomplete set while the (freshly
        # rendered) page promised the new behaviour.
        try:
            r = clear(dry=args.dry, force=args.force, factory=args.factory)
            print(_json.dumps({"ok": True, **r}))
            return 0
        except SystemExit as e:                        # a run looks active — refusal
            if isinstance(e.code, int):
                raise
            print(_json.dumps({"ok": False, "error": str(e), "can_force": True}))
            return 9
    r = clear(dry=args.dry, force=args.force, factory=args.factory)
    verb = "would remove" if args.dry else "removed"
    print(f"{verb} {r['removed']} generated file(s):")
    for t in r["targets"]:
        print(f"  {t}")
    if not args.dry:
        print("estate kept: registry (repo config — remove repos in the "
              "Repositories view), catalog/bootstrap code, AGENTS.md, demo repos, "
              "knowledge/repos team notes.")
        print("Rebuild demo state with: make demo-bootstrap && make demo-pr")
        print("Repo guidance: knowledge/generated/ rebuilds itself on the next "
              "AGENTS.md regeneration; re-pull repo-owned files with make sync-guidance")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    raise SystemExit(main())
