#!/usr/bin/env python3
"""Pull the phase's JSON contract out of a claude -p json result and validate
required keys against a minimal schema (stdlib-only check).

The contract is the LAST valid JSON object in the result text that carries the
schema's required keys — prose may contain other brace-blobs (code snippets,
single-quoted JS objects, deeply nested examples), so parse candidates instead
of trusting a regex match (parity finding P7)."""
import json, sys

raw = json.load(open(sys.argv[1], encoding="utf-8"))
text = raw.get("result", "") if isinstance(raw, dict) else str(raw)
schema = json.load(open(sys.argv[2], encoding="utf-8"))
required = schema.get("required", [])

decoder = json.JSONDecoder()
best = None
for i, ch in enumerate(text):
    if ch != "{":
        continue
    try:
        obj, _ = decoder.raw_decode(text, i)
    except json.JSONDecodeError:
        continue
    if isinstance(obj, dict) and all(k in obj for k in required):
        best = obj                      # keep scanning: LAST matching object wins
if best is None:
    sys.exit(f"NO_CONTRACT_JSON (no object with keys {required})")
print(json.dumps(best, indent=2))
