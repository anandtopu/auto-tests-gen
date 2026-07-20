#!/usr/bin/env python3
"""Harvest correlation facts from all registered app repos: OpenAPI paths per
backend repo, route tables per frontend repo. For the scaffold this reads local
clones under workspace/src/ when present; TODO wire to Scm.clone_ro for CI."""
import json, pathlib, re, sys
sys.path.insert(0, "engine/lib"); from registry import load_registry

reg, facts = load_registry(), {"endpoints": {}, "routes": {}}
for r in reg["source_repositories"]:
    base = pathlib.Path("workspace/src") / r["name"]
    if r["type"] == "backend" and (base / r.get("contract", "")).exists():
        spec = (base / r["contract"]).read_text()
        for path in re.findall(r"^\s{2}(/[^:\s]+):", spec, re.M):
            facts["endpoints"].setdefault(path, []).append(r["name"])
    if r["type"] == "frontend" and (base / r.get("route_table", "")).exists():
        src = (base / r["route_table"]).read_text()
        for route in re.findall(r"path:\s*['\"]([^'\"]+)", src):
            facts["routes"].setdefault(route, []).append(r["name"])
print(json.dumps(facts, indent=2))
