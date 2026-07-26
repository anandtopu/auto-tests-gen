#!/usr/bin/env python3
"""Run one benchmark fixture through resolve (cheap, always) and optionally the full
pipeline (RUN_FULL=1). Compares resolution against fixture expectations."""
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
ok = set(got["test_repos"]) == set(fx["expected"]["test_repos"])
print(json.dumps({"fixture": sys.argv[1], "routing_ok": ok,
                  "got": got["test_repos"], "expected": fx["expected"]["test_repos"]}))
if os.environ.get("RUN_FULL") == "1":
    # bash_exe(): plain "bash" resolves to WSL's System32 stub outside Git Bash
    from work_queue import bash_exe
    args = [bash_exe(), "engine/pipeline.sh", fx["mode"]]
    if fx["mode"] == "pr":
        args += [fx["repo"], str(fx["pr"])]
    else:
        args.append(fx["key"])
    subprocess.run(args, stdin=subprocess.DEVNULL)
