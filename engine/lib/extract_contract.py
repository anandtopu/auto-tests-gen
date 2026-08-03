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
best_end = -1                           # end offset of the accepted candidate
for i, ch in enumerate(text):
    if ch != "{":
        continue
    if i < best_end:
        # Inside the object we already accepted, so this is one of its own
        # members — and a phase that documents itself ("here is the contract,
        # and here is an example of a test") nests an object carrying the very
        # same required keys. Left-to-right + last-wins handed the win to that
        # EXAMPLE, silently replacing the phase's real output. Verified: a
        # contract with an `example` block extracted the example.
        continue
    try:
        obj, end = decoder.raw_decode(text, i)
    except json.JSONDecodeError:
        continue
    if isinstance(obj, dict) and all(k in obj for k in required):
        # Still LAST-wins among SIBLINGS: prose ahead of the contract may hold
        # brace-blobs, so a later top-level object legitimately supersedes an
        # earlier one (parity finding P7). Only nesting is excluded.
        best, best_end = obj, end
if best is None:
    sys.exit(f"NO_CONTRACT_JSON (no object with keys {required})")
print(json.dumps(best, indent=2))
