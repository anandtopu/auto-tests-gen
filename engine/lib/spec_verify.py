#!/usr/bin/env python3
"""Read-only spec re-verification (SDD story 4.2).

For a (typically stale) spec: are the key's EXISTING committed tests still
passing against the current app, without generating anything or touching the
gate? One command answers "still passing" vs "actually broken by the contract
change":

  1. find the key's tests in the catalog (mapping.feature == KEY);
  2. clone each owning test repo READ-ONLY via the Scm port;
  3. run exactly those spec files with the repo's own test command inside the
     provisioned environment (bin/with-env.sh — same rails as the gate, no
     commit path anywhere);
  4. attach {passed, failed, ts} to the plan state as `verification_run`.

CLI / make spec-verify KEY=..
"""
import app_paths
import json
import os

import pathlib
import subprocess
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import work_queue
import env_flag                     # AIQE_MOCK means what it says

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _tests_for(key):
    """{test_repo: [spec files]} from catalog evidence (feature == key)."""
    out = {}
    for f in app_paths.catalog_files(ROOT):
        for line in open(f, encoding="utf-8"):
            if not line.strip():
                continue
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if (e.get("mapping") or {}).get("feature") == key:
                out.setdefault(e.get("test_repo"), []).append(e.get("file"))
    return {k: sorted(set(v)) for k, v in out.items() if k}


def verify(key):
    """{repo: {passed, log}} — read-only throughout.

    `passed` has THREE states, not two. True and False mean the tests ran and
    said so; **None means we could not find out** — the clone failed, or the
    mapped spec files are not in the repo. The first version recorded both of
    those as `passed: False`, which reads as "these tests are broken" when the
    truth is "we never ran them". That is the difference between a reviewer
    investigating a regression and a reviewer fixing a mapping.
    """
    mock = env_flag.mock()
    scm = ROOT / ("adapters/mock/scm.sh" if mock else "adapters/scm/github.sh")
    results = {}
    for repo, files in _tests_for(key).items():
        dest = ROOT / "out" / f"spec-verify-{repo}"
        r = subprocess.run([work_queue.bash_exe(), str(scm), "clone_ro",
                            repo, str(dest)], cwd=ROOT, capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           stdin=subprocess.DEVNULL)
        if r.returncode != 0 or not dest.is_dir():
            # Not a test failure: nothing was executed.
            results[repo] = {"passed": None, "unverifiable": True,
                             "log": f"clone_ro failed: {r.stderr.strip()[:200]}"}
            continue
        try:
            import yaml
            cfg = yaml.safe_load(open(dest / ".ai-qe/config.yaml",
                                      encoding="utf-8")) or {}
            test_cmd = (cfg.get("commands") or {}).get("test", "node --test")
        except Exception:
            test_cmd = "node --test"
        present = [f for f in files if (dest / f).exists()]
        if not present:
            # The catalog points at files this repo does not have — a mapping
            # problem to fix, not a regression to investigate.
            results[repo] = {"passed": None, "unverifiable": True,
                             "log": "no mapped spec files exist in the repo "
                                    "(stale catalog mapping, not a failure)"}
            continue
        # Same contract as the gate: run INSIDE the repo with TREPO_DIR="."
        # — with-env embeds the path in a python -c string, and a Windows
        # absolute path there is a SyntaxError.
        run = subprocess.run(
            [work_queue.bash_exe(), str(ROOT / "bin/with-env.sh"), ".", "--",
             "bash", "-c", f"{test_cmd} {' '.join(present)}"],
            cwd=dest, capture_output=True, text=True, encoding="utf-8",
            errors="replace", stdin=subprocess.DEVNULL, timeout=600)
        results[repo] = {"passed": run.returncode == 0, "unverifiable": False,
                         "log": (run.stdout + run.stderr)[-400:]}
    # Attach to plan state — information for the reviewer, gating nothing.
    try:
        import fs_lock
        import plan_state
        with fs_lock.lock(plan_state.FILE):
            state = plan_state.load()
            e = state.get(key, {"history": []})
            # An overall "passed" only when something actually ran AND
            # everything that ran passed. If any repo was unverifiable the
            # answer is None: reporting True would claim coverage nobody
            # checked, and False would blame tests that never executed.
            ran = [v for v in results.values() if v["passed"] is not None]
            e["verification_run"] = {
                "ts": time.time(),
                "passed": (all(v["passed"] for v in ran)
                           if ran and len(ran) == len(results) else None),
                "unverifiable": [k for k, v in results.items()
                                 if v["passed"] is None],
                "repos": {k: v["passed"] for k, v in results.items()}}
            state[key] = e
            plan_state._save(state)
    except Exception:
        pass
    return results


def main(argv):
    sys.stdout.reconfigure(encoding="utf-8")
    if not argv:
        print("usage: spec_verify.py <KEY>", file=sys.stderr)
        return 64
    key = argv[0]
    results = verify(key)
    if not results:
        print(f"spec-verify {key}: no cataloged tests map to this key")
        return 1
    failed = unverified = False
    for repo, v in results.items():
        # UNVERIFIED is its own word. Printing FAIL for a repo that never ran a
        # test sends a reviewer hunting for a regression that does not exist.
        state = ("UNVERIFIED" if v["passed"] is None
                 else "PASS" if v["passed"] else "FAIL")
        print(f"{repo}: {state}")
        if v["passed"] is None:
            unverified = True
        elif not v["passed"]:
            failed = True
        if v["passed"] is not True:
            print("  " + v["log"].replace("\n", "\n  ")[:300])
    if failed:
        return 1
    # Exit 2 for "nothing was established" — distinct from a real failure, so a
    # script can tell "the tests broke" from "we could not run them".
    return 2 if unverified else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
