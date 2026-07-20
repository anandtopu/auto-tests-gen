#!/usr/bin/env python3
"""Stage 1 — deterministic extraction from Playwright specs (regex-lite AST proxy).
Pulls: titles, tags, HTTP endpoints, UI routes, page objects, fixtures."""
import json, pathlib, re, sys

repo_dir, trepo = pathlib.Path(sys.argv[1]), sys.argv[2]
RX = {
  "title":    re.compile(r"test\(\s*['\"]([^'\"]+)"),
  "tags":     re.compile(r"@[\w-]+"),
  "endpoint": re.compile(r"\.(get|post|put|patch|delete)\(\s*[`'\"](/[^`'\"\s]*)", re.I),
  "route":    re.compile(r"goto\(\s*[`'\"]([^`'\"]+)"),
  "pageobj":  re.compile(r"from\s+['\"].*pages/(\w+)"),
  "fixture":  re.compile(r"['\"]((?:data|fixtures)/[^'\"]+)"),
}
for spec in repo_dir.rglob("*.spec.[tj]s"):
    src = spec.read_text(errors="ignore")
    for title in RX["title"].findall(src) or [spec.stem]:
        print(json.dumps({
            "test_id": f"{trepo}::{spec.relative_to(repo_dir)}::{title}",
            "test_repo": trepo, "file": str(spec.relative_to(repo_dir)), "title": title,
            "layer": "api" if RX["endpoint"].search(src) and not RX["route"].search(src) else "ui",
            "tags": sorted(set(RX["tags"].findall(src))),
            "evidence": {
                "endpoints": [f"{m[0].upper()} {m[1]}" for m in RX["endpoint"].findall(src)],
                "ui_routes": RX["route"].findall(src),
                "page_objects": sorted(set(RX["pageobj"].findall(src))),
                "fixtures": sorted(set(RX["fixture"].findall(src))),
                "git_jira_keys": [],   # filled by correlate.py via git log
            }}))
