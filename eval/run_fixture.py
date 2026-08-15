#!/usr/bin/env python3
"""Run one benchmark fixture through resolve (cheap, always) and optionally the full
pipeline (RUN_FULL=1). Compares resolution against fixture expectations.

EVERY declared expectation is compared, not just `test_repos`. It used to be
the one field, so "Routing accuracy: 100%" was a claim about a single key --
and two resolutions that agree on `test_repos: []` can disagree about
everything that matters: an EMPTY change list (nothing was established, so
confidence 0.0) versus a change list with nothing testable in it (an
established negative, confidence 1.0). The resolver was taught that
distinction deliberately; the benchmark measuring it could not see it.

An expectation naming a key the resolution does not answer is REPORTED as
`unchecked` rather than passed over -- `expected.impact` has sat in a shipped
fixture all along and nothing read it, which is the same written-but-unread
shape this benchmark exists to catch in the product. It does not fail the
fixture (that would break a fixture asserting something a future full-pipeline
run will check), but it can no longer be invisible.
"""
import json, os, pathlib, subprocess, sys, tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "engine/lib"))

fx = json.load(open(sys.argv[1], encoding="utf-8"))
if fx["mode"] == "pr":
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False,
                                     encoding="utf-8") as tf:
        tf.write("\n".join(fx["changed_files"]))
    try:
        r = subprocess.run([sys.executable, "engine/phases/resolve.py", "pr", fx["repo"],
                            "--changed-files", tf.name], capture_output=True, text=True,
                           encoding="utf-8", stdin=subprocess.DEVNULL)
    finally:
        os.unlink(tf.name)
else:
    r = subprocess.run([sys.executable, "engine/phases/resolve.py", "jira", fx["key"],
                        "--components", ",".join(fx.get("components", [])),
                        "--labels", ",".join(fx.get("labels", []))],
                       capture_output=True, text=True, encoding="utf-8",
                       stdin=subprocess.DEVNULL)
got = json.loads(r.stdout)


def _same(have, want):
    """Lists compare as sets (routing order is not a claim); everything else
    exactly."""
    if isinstance(want, list) and isinstance(have, list):
        return set(have) == set(want)
    return have == want


expected = fx.get("expected") or {}
compared, mismatched, unchecked = {}, [], []
for key, want in expected.items():
    if key not in got:
        unchecked.append(key)
        continue
    compared[key] = _same(got[key], want)
    if not compared[key]:
        mismatched.append({"field": key, "got": got[key], "expected": want})

# `ok` requires at least one comparison: a fixture whose every expectation
# names an unanswerable key would otherwise score a vacuous pass, which is the
# failure mode this file is being fixed for.
ok = bool(compared) and all(compared.values())
print(json.dumps({"fixture": sys.argv[1], "routing_ok": ok,
                  "compared": sorted(compared), "mismatched": mismatched,
                  "unchecked_expectations": sorted(unchecked),
                  "got": got["test_repos"],
                  "expected": expected.get("test_repos", [])}))
if os.environ.get("RUN_FULL") == "1":
    # bash_exe(): plain "bash" resolves to WSL's System32 stub outside Git Bash
    from work_queue import bash_exe
    args = [bash_exe(), "engine/pipeline.sh", fx["mode"]]
    if fx["mode"] == "pr":
        args += [fx["repo"], str(fx["pr"])]
    else:
        args.append(fx["key"])
    subprocess.run(args, stdin=subprocess.DEVNULL)
