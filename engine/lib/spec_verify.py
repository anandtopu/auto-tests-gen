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
import glob
import json
import os
import pathlib
import subprocess
import sys
import time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import work_queue

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _tests_for(key):
    """{test_repo: [spec files]} from catalog evidence (feature == key)."""
    out = {}
    for f in sorted(glob.glob(str(ROOT / "catalog/*.jsonl"))):
        if pathlib.Path(f).name == "catalog.sample.jsonl":
            continue
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
    """{repo: {passed: bool, log: str}} — read-only throughout."""
    mock = os.environ.get("AIQE_MOCK", "1") == "1"
    scm = ROOT / ("adapters/mock/scm.sh" if mock else "adapters/scm/github.sh")
    results = {}
    for repo, files in _tests_for(key).items():
        dest = ROOT / "out" / f"spec-verify-{repo}"
        r = subprocess.run([work_queue.bash_exe(), str(scm), "clone_ro",
                            repo, str(dest)], cwd=ROOT, capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           stdin=subprocess.DEVNULL)
        if r.returncode != 0 or not dest.is_dir():
            results[repo] = {"passed": False,
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
            results[repo] = {"passed": False,
                             "log": "no mapped spec files exist in the repo"}
            continue
        # Same contract as the gate: run INSIDE the repo with TREPO_DIR="."
        # — with-env embeds the path in a python -c string, and a Windows
        # absolute path there is a SyntaxError.
        run = subprocess.run(
            [work_queue.bash_exe(), str(ROOT / "bin/with-env.sh"), ".", "--",
             "bash", "-c", f"{test_cmd} {' '.join(present)}"],
            cwd=dest, capture_output=True, text=True, encoding="utf-8",
            errors="replace", stdin=subprocess.DEVNULL, timeout=600)
        results[repo] = {"passed": run.returncode == 0,
                         "log": (run.stdout + run.stderr)[-400:]}
    # Attach to plan state — information for the reviewer, gating nothing.
    try:
        import fs_lock
        import plan_state
        with fs_lock.lock(plan_state.FILE):
            state = plan_state.load()
            e = state.get(key, {"history": []})
            e["verification_run"] = {
                "ts": time.time(),
                "passed": all(v["passed"] for v in results.values()) if results
                else None,
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
    ok = True
    for repo, v in results.items():
        print(f"{repo}: {'PASS' if v['passed'] else 'FAIL'}")
        if not v["passed"]:
            ok = False
            print("  " + v["log"].replace("\n", "\n  ")[:300])
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
