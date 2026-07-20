#!/usr/bin/env python3
"""Stage 5 — regenerate registry test_repositories[].covers from the catalog.
Only confirmed/auto mappings feed routing (ADR: never route on needs_review)."""
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
for t in reg["test_repositories"]:
    t["covers"] = sorted(cov[t["name"]])
reg_path.write_text(yaml.safe_dump(reg, sort_keys=False))
print("coverage maps regenerated:", {k: sorted(v) for k, v in cov.items()})
