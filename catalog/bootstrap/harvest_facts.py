#!/usr/bin/env python3
"""Harvest correlation facts from all registered app repos: OpenAPI paths per
backend repo, route tables per frontend repo, read from clones under
workspace/src/ (run_bootstrap.sh Stage 0b puts them there via the Scm port).

REPORTS WHAT IT COULD NOT READ. These facts are the only thing that attributes
a test to an app repo: no facts means every test correlates to nothing, tiers
to `orphan`, and regen_coverage writes `covers: []` — which silently unroutes
the whole repo. In the output that is indistinguishable from an estate whose
tests genuinely cover nothing recognizable.

It was not hypothetical. Nothing in run_bootstrap.sh ever populated
workspace/src/ — only bin/demo-bootstrap.sh did, by copying demo/. So the real
chain harvested an empty fact set on every run, produced an all-orphan catalog,
and printed "Bootstrap complete". Constitution C13: an inability to establish a
fact is never reported as an established negative.
"""
import json, pathlib, re, sys
sys.path.insert(0, "engine/lib"); from registry import load_registry

reg, facts = load_registry(), {"endpoints": {}, "routes": {}}
declared, harvested, missing = [], [], []
for r in reg["source_repositories"]:
    base = pathlib.Path("workspace/src") / r["name"]
    # Bind the artifact path first: `base / ""` equals `base`, so a repo registered
    # without contract/route_table would pass the exists() check and then KeyError.
    contract = r.get("contract")
    route_table = r.get("route_table")
    artifact = contract if r["type"] == "backend" else route_table
    if not artifact:
        continue                      # nothing declared: not a gap, just absent
    declared.append(r["name"])
    if not (base / artifact).exists():
        missing.append(f"{r['name']} ({base / artifact})")
        continue
    harvested.append(r["name"])
    if r["type"] == "backend":
        spec = (base / contract).read_text(encoding="utf-8", errors="replace")
        for path in re.findall(r"^\s{2}(/[^:\s]+):", spec, re.M):
            facts["endpoints"].setdefault(path, []).append(r["name"])
    else:
        src = (base / route_table).read_text(encoding="utf-8", errors="replace")
        for route in re.findall(r"path:\s*['\"]([^'\"]+)", src):
            facts["routes"].setdefault(route, []).append(r["name"])

print(f"harvest_facts: {len(facts['endpoints'])} endpoints, {len(facts['routes'])} routes "
      f"from {len(harvested)}/{len(declared)} app repos declaring an artifact",
      file=sys.stderr)
if missing:
    print("harvest_facts: NOT READ - " + "; ".join(missing), file=sys.stderr)

# Declared artifacts of which we read NONE is a harvest failure, not a finding
# about the tests. Continuing would tier every test `orphan` and strip covers:.
if declared and not harvested:
    print("HARVEST_FAILED: none of the "
          f"{len(declared)} app repo(s) declaring a contract/route table could be "
          "read under workspace/src/. Correlation would attribute nothing and "
          "every test would be cataloged `orphan`, stripping covers: and "
          "unrouting the repo. Fix: let Stage 0b clone the app repos (check SCM "
          "credentials / SCM_KIND), or run with AIQE_MOCK=1 against the demo "
          "estate.", file=sys.stderr)
    sys.exit(3)
print(json.dumps(facts, indent=2))
