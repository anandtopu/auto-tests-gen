#!/usr/bin/env python3
"""Harvest correlation facts from all registered app repos: OpenAPI paths per
backend repo, route tables per frontend repo. For the scaffold this reads local
clones under workspace/src/ when present; TODO wire to Scm.clone_ro for CI."""
import json, pathlib, re, sys
sys.path.insert(0, "engine/lib"); from registry import load_registry

reg, facts = load_registry(), {"endpoints": {}, "routes": {}}
for r in reg["source_repositories"]:
    base = pathlib.Path("workspace/src") / r["name"]
    # Bind the artifact path first: `base / ""` equals `base`, so a repo registered
    # without contract/route_table would pass the exists() check and then KeyError.
    contract = r.get("contract")
    if r["type"] == "backend" and contract and (base / contract).exists():
        spec = (base / contract).read_text(encoding="utf-8", errors="replace")
        for path in re.findall(r"^\s{2}(/[^:\s]+):", spec, re.M):
            facts["endpoints"].setdefault(path, []).append(r["name"])
    route_table = r.get("route_table")
    if r["type"] == "frontend" and route_table and (base / route_table).exists():
        src = (base / route_table).read_text(encoding="utf-8", errors="replace")
        for route in re.findall(r"path:\s*['\"]([^'\"]+)", src):
            facts["routes"].setdefault(route, []).append(r["name"])
print(json.dumps(facts, indent=2))
