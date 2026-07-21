#!/usr/bin/env python3
"""Aggregate eval/results into the PoC scorecard (architecture §8)."""
import glob, json, sys
sys.stdout.reconfigure(encoding="utf-8")   # Windows consoles default to cp1252
res = [json.load(open(f)) for f in glob.glob("eval/results/*.json")]
if not res:
    print("no results — run `make eval` after adding benchmark fixtures"); raise SystemExit
routing = sum(r["routing_ok"] for r in res) / len(res)
print(f"Routing accuracy: {routing:.0%} across {len(res)} fixtures (target ≥95%)")
# TODO post-PoC: acceptance rate, mutation validity, duplicate rate from run records
