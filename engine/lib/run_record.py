#!/usr/bin/env python3
"""Assemble the structured run record (architecture §8) from out/*.json.
Includes per-test-repo gate outcomes (out/gate_results.tsv) so persisted records
in reports/runs/ carry everything the QA monitoring surfaces need."""
import glob, json, os, pathlib, sys, time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import critic as critic_lib

run_id, mode, key = sys.argv[1:4]
phases = []
for f in sorted(glob.glob("out/*.contract.json")):
    name = os.path.basename(f).replace(".contract.json", "")
    phases.append({"name": name, "contract": json.load(open(f, encoding="utf-8"))})

gates = []
if os.path.exists("out/gate_results.tsv"):
    for line in open("out/gate_results.tsv", encoding="utf-8"):
        if not line.strip():
            continue
        repo, status, exit_code, sha = (line.rstrip("\n").split("\t") + ["", "", "", ""])[:4]
        diff = f"reports/runs/{run_id}-{repo}.diff"
        gates.append({"test_repo": repo, "status": status, "exit_code": int(exit_code),
                      "commit": sha or None,
                      "log": f"reports/{key}-{repo}.log",
                      "diff": diff if os.path.exists(diff) else None})

overall = ("quarantined" if any(g["status"] == "quarantined" for g in gates)
           else "committed" if any(g["status"] == "committed" for g in gates)
           else "no_changes")
# Advisory critic score lifted to the top level so the scorecard and dashboard don't
# have to dig through phases[]. `overall` above is computed purely from gate outcomes —
# the critic never contributes to it (openhands-review §3.2).
record = {"run_id": run_id, "trigger": {"type": mode, "key": key},
          "ts": time.time(), "overall": overall,
          "gates": gates, "phases": phases}
signal = critic_lib.load()
if signal:
    record["critic"] = signal
print(json.dumps(record))
