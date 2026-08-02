#!/usr/bin/env python3
"""Stage 5 — regenerate registry test_repositories[].covers from the catalog.
Only confirmed/auto mappings feed routing (ADR: never route on needs_review).
covers = catalog evidence UNION the repo's declared `scope` (hand-managed via
repo_admin / the dashboard Repositories view) — so a newly-mapped app repo
routes to its test repo before any test evidence exists."""
import glob, json, os, pathlib, sys, yaml
sys.path.insert(0, "engine/lib")
import app_paths                      # R12: mutable paths resolve here
reg_path = app_paths.registry_file()
reg = yaml.safe_load(reg_path.read_text(encoding="utf-8"))
cov = {t["name"]: set() for t in reg["test_repositories"]}
for f in glob.glob(str(app_paths.catalog_dir() / "*.jsonl")):
    if pathlib.Path(f).name == "catalog.sample.jsonl":   # fixture, not evidence
        continue
    for l in open(f, encoding="utf-8"):
        if not l.strip():                                # tolerate trailing blanks
            continue
        e = json.loads(l)
        if e["mapping"]["status"] in ("confirmed", "auto") and e["test_repo"] in cov:
            cov[e["test_repo"]].update(e["mapping"]["app_repos"])
known = {r["name"] for r in reg["source_repositories"]}
for t in reg["test_repositories"]:
    t["covers"] = sorted(cov[t["name"]] | (set(t.get("scope", [])) & known))
# Atomic write: every load_registry() consumer reads this file — never leave a
# half-dumped YAML behind. Serialization against concurrent mutators is the
# CALLER's job (repo_admin/qa.py hold fs_lock on the registry while running this
# script; locking here would deadlock against the parent's own lock).
tmp = reg_path.with_suffix(".yaml.tmp")
tmp.write_text(yaml.safe_dump(reg, sort_keys=False), encoding="utf-8")
os.replace(tmp, reg_path)
print("coverage maps regenerated:", {k: sorted(v) for k, v in cov.items()})
