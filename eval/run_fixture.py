#!/usr/bin/env python3
"""Run one benchmark fixture through resolve (cheap, always) and optionally the full
pipeline (RUN_FULL=1). Compares resolution against fixture expectations."""
import json, os, subprocess, sys, tempfile
fx = json.load(open(sys.argv[1]))
if fx["mode"] == "pr":
    with tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False) as tf:
        tf.write("\n".join(fx["changed_files"]))
    r = subprocess.run([sys.executable, "engine/phases/resolve.py", "pr", fx["repo"],
                        "--changed-files", tf.name], capture_output=True, text=True)
else:
    r = subprocess.run([sys.executable, "engine/phases/resolve.py", "jira", fx["key"],
                        "--components", ",".join(fx.get("components", [])),
                        "--labels", ",".join(fx.get("labels", []))],
                       capture_output=True, text=True)
got = json.loads(r.stdout)
ok = set(got["test_repos"]) == set(fx["expected"]["test_repos"])
print(json.dumps({"fixture": sys.argv[1], "routing_ok": ok,
                  "got": got["test_repos"], "expected": fx["expected"]["test_repos"]}))
if os.environ.get("RUN_FULL") == "1":
    subprocess.run(["bash", "engine/pipeline.sh", fx["mode"],
                    fx.get("repo", fx.get("key", "")), str(fx.get("pr", ""))])
