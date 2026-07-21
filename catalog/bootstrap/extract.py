#!/usr/bin/env python3
"""Stage 1 — deterministic extraction from Playwright specs (regex-lite AST proxy).
Pulls: titles, tags, HTTP endpoints, UI routes, page objects, fixtures."""
import json, pathlib, re, sys

repo_dir, trepo = pathlib.Path(sys.argv[1]), sys.argv[2]
RX = {
  "title":    re.compile(r"test\(\s*['\"]([^'\"]+)"),
  "tags":     re.compile(r"@[\w-]+"),
  "endpoint": re.compile(r"\.(get|post|put|patch|delete)\(\s*[`'\"](/[^`'\"\s]*)", re.I),
  "fetch":    re.compile(r"fetch\(\s*[`'\"](?:\$\{[A-Za-z_]+\})?(/[^`'\"\s?]*)[`'\"]?(.{0,140})", re.S),
  "method":   re.compile(r"method:\s*['\"](\w+)['\"]"),
  "route":    re.compile(r"goto\(\s*[`'\"]([^`'\"]+)"),
  "pageobj":  re.compile(r"from\s+['\"].*pages/(\w+)"),
  "fixture":  re.compile(r"['\"]((?:data|fixtures)/[^'\"]+)"),
}
for spec in repo_dir.rglob("*.spec.[tj]s"):
    src = spec.read_text(errors="ignore")
    rel = spec.relative_to(repo_dir).as_posix()   # forward slashes on every platform
    has_api = RX["endpoint"].search(src) or RX["fetch"].search(src)
    for title in RX["title"].findall(src) or [spec.stem]:
        print(json.dumps({
            "test_id": f"{trepo}::{rel}::{title}",
            "test_repo": trepo, "file": rel, "title": title,
            "layer": "api" if has_api and not RX["route"].search(src) else "ui",
            "tags": sorted(set(RX["tags"].findall(src))),
            "evidence": {
                "endpoints": sorted(set(
                    [f"{m[0].upper()} {m[1]}" for m in RX["endpoint"].findall(src)] +
                    [f"{(RX['method'].search(tail) or type('x',(),{'group':lambda s,i:'GET'})()).group(1).upper()} {path}"
                     for path, tail in RX["fetch"].findall(src)])),
                "ui_routes": RX["route"].findall(src),
                "page_objects": sorted(set(RX["pageobj"].findall(src))),
                "fixtures": sorted(set(RX["fixture"].findall(src))),
                "git_jira_keys": [],   # filled by correlate.py via git log
            }}))
