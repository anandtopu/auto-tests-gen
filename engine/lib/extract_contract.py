#!/usr/bin/env python3
"""Pull the last JSON object out of a claude -p json result and validate
required keys against a minimal schema (stdlib-only check)."""
import json, re, sys

raw = json.load(open(sys.argv[1]))
text = raw.get("result", "") if isinstance(raw, dict) else str(raw)
matches = re.findall(r"\{(?:[^{}]|\{[^{}]*\})*\}", text, re.S)
if not matches:
    sys.exit("NO_CONTRACT_JSON")
obj = json.loads(matches[-1])
schema = json.load(open(sys.argv[2]))
missing = [k for k in schema.get("required", []) if k not in obj]
if missing:
    sys.exit(f"CONTRACT_MISSING_KEYS: {missing}")
print(json.dumps(obj, indent=2))
