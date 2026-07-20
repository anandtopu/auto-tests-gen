#!/usr/bin/env python3
"""Split correlated entries: confident ones pass through; residue goes to LLM."""
import json, sys
ws = sys.argv[2]
resolved, residue = open(f"{ws}/resolved.jsonl", "w"), open(f"{ws}/residue.jsonl", "w")
for l in open(sys.argv[1]):
    e = json.loads(l)
    (resolved if e["mapping"]["confidence"] >= 0.55 else residue).write(l)
