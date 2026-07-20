#!/usr/bin/env python3
"""Phase 0 — Repo Resolution (architecture §5.8.2).
Rules-first; emits resolution JSON. LLM fallback is invoked by pipeline.sh
only when confidence < threshold (see prompts/resolve-llm.md).

Usage:
  resolve.py pr   <source_repo> --changed-files files.txt
  resolve.py jira <KEY> --components "Checkout,Catalog" --labels "api-only" [--linked-repos "orders-api"]
"""
import argparse, fnmatch, json, sys
sys.path.insert(0, __file__.rsplit("/phases", 1)[0] + "/lib")
from registry import load_registry, load_org_config, source_repo, test_repos_for

def resolve_pr(reg, repo_name, changed):
    src = source_repo(reg, repo_name)
    if not src:
        return dict(source_repos=[], test_repos=[], cross_repo_impact=[],
                    confidence=0.0, rationale=f"{repo_name} not in registry")
    testable = any(fnmatch.fnmatch(f, p) for f in changed for p in src.get("testable_paths", ["**"]))
    if not testable:
        return dict(source_repos=[repo_name], test_repos=[], cross_repo_impact=[],
                    confidence=1.0, rationale="no testable paths changed", skip=True)
    sources, impact = [repo_name], []
    tests = set(test_repos_for(reg, repo_name))
    contract = src.get("contract")
    if contract and any(f == contract or f.startswith(contract.rsplit("/", 1)[0]) for f in changed):
        for consumer in src.get("consumed_by", []):
            sources.append(consumer)
            ui = test_repos_for(reg, consumer, layers=["ui"])
            tests.update(ui)
            impact.append({"cause": f"contract change in {contract}",
                           "consumer": consumer, "test_repos": ui})
    return dict(source_repos=sources, test_repos=sorted(tests), cross_repo_impact=impact,
                confidence=1.0 if tests else 0.4,
                rationale="registry rule: repo->coverage" + (" + contract fan-out" if impact else ""))

def resolve_jira(reg, key, components, labels, linked_repos):
    hints = reg.get("routing_hints", {})
    sources = set(linked_repos)                    # dev-panel evidence wins
    for c in components:
        sources.update(hints.get("jira_component_map", {}).get(c, []))
    layers = None
    for l in labels:
        r = hints.get("jira_label_map", {}).get(l, {})
        if "restrict_layers" in r:
            layers = r["restrict_layers"]
    tests = set()
    for s in sources:
        tests.update(test_repos_for(reg, s, layers=layers))
    conf = 0.95 if linked_repos else (0.85 if sources else 0.2)
    return dict(source_repos=sorted(sources), test_repos=sorted(tests), cross_repo_impact=[],
                confidence=conf if tests else min(conf, 0.4),
                rationale=f"components={components} labels={labels} linked={linked_repos}")

if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("mode", choices=["pr", "jira"]); ap.add_argument("target")
    ap.add_argument("--changed-files"); ap.add_argument("--components", default="")
    ap.add_argument("--labels", default=""); ap.add_argument("--linked-repos", default="")
    a = ap.parse_args()
    reg = load_registry()
    if a.mode == "pr":
        changed = [l.strip() for l in open(a.changed_files)] if a.changed_files else []
        out = resolve_pr(reg, a.target, changed)
    else:
        out = resolve_jira(reg, a.target,
                           [c for c in a.components.split(",") if c],
                           [l for l in a.labels.split(",") if l],
                           [r for r in a.linked_repos.split(",") if r])
    th = load_org_config()["resolution"]["confidence_threshold"]
    out["needs_clarification"] = out["confidence"] < th and not out.get("skip")
    print(json.dumps(out, indent=2))
