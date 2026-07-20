#!/usr/bin/env python3
"""Assemble the structured run record (architecture §8) from out/*.json."""
import glob, json, sys, time
run_id, mode, key = sys.argv[1:4]
phases = []
for f in sorted(glob.glob("out/*.contract.json")):
    name = f.split("/")[-1].replace(".contract.json", "")
    phases.append({"name": name, "contract": json.load(open(f))})
print(json.dumps({"run_id": run_id, "trigger": {"type": mode, "key": key},
                  "ts": time.time(), "phases": phases}))
