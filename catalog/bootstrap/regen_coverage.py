#!/usr/bin/env python3
"""Stage 5 — regenerate registry test_repositories[].covers from the catalog.
Only confirmed/auto mappings feed routing (ADR: never route on needs_review).
covers = catalog evidence UNION the repo's declared `scope` (hand-managed via
repo_admin / the dashboard Repositories view) — so a newly-mapped app repo
routes to its test repo before any test evidence exists."""
import glob, json, pathlib, sys, yaml
sys.path.insert(0, "engine/lib")
reg_path = pathlib.Path("registry/repo-registry.yaml")
reg = yaml.safe_load(reg_path.read_text())
cov = {t["name"]: set() for t in reg["test_repositories"]}
for f in glob.glob("catalog/*.jsonl"):
    for l in open(f):
        e = json.loads(l)
        if e["mapping"]["status"] in ("confirmed", "auto") and e["test_repo"] in cov:
            cov[e["test_repo"]].update(e["mapping"]["app_repos"])
known = {r["name"] for r in reg["source_repositories"]}
for t in reg["test_repositories"]:
    t["covers"] = sorted(cov[t["name"]] | (set(t.get("scope", [])) & known))
reg_path.write_text(yaml.safe_dump(reg, sort_keys=False))
print("coverage maps regenerated:", {k: sorted(v) for k, v in cov.items()})
