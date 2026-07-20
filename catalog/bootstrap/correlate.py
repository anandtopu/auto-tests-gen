#!/usr/bin/env python3
"""Stage 2 — deterministic joins: endpoints<->contracts, routes<->route tables,
JIRA keys from git history of the test file."""
import json, re, subprocess, sys

entries = [json.loads(l) for l in open(sys.argv[1])]
facts = json.load(open(sys.argv[2]))

def norm(p):  # /v1/orders/123/discounts -> /v1/orders/{id}/discounts
    return re.sub(r"/\d+", "/{id}", p)

for e in entries:
    repos, methods = set(), []
    for ep in e["evidence"]["endpoints"]:
        path = norm(ep.split(" ", 1)[1])
        hit = facts["endpoints"].get(path) or facts["endpoints"].get(path.rstrip("/"))
        if hit: repos.update(hit); methods.append("contract_match")
    for rt in e["evidence"]["ui_routes"]:
        hit = facts["routes"].get(norm(rt))
        if hit: repos.update(hit); methods.append("route_match")
    try:
        log = subprocess.run(["git", "-C", f"workspace/bootstrap/{e['test_repo']}/repo",
                              "log", "--format=%s", "--", e["file"]],
                             capture_output=True, text=True, timeout=30).stdout
        keys = sorted(set(re.findall(r"[A-Z][A-Z0-9]+-\d+", log)))
        e["evidence"]["git_jira_keys"] = keys
        if keys: methods.append("git_history")
    except Exception:
        pass
    conf = min(0.99, 0.55 + 0.2 * len(set(methods))) if repos else 0.0
    e["mapping"] = {"app_repos": sorted(repos), "services": sorted(repos),
                    "domain": (e["tags"][0].lstrip("@") if e["tags"] else ""),
                    "feature": (e["evidence"]["git_jira_keys"] or [""])[0],
                    "confidence": round(conf, 2), "method": sorted(set(methods)) or ["none"],
                    "status": "pending"}
    print(json.dumps(e))
