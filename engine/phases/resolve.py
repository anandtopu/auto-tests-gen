#!/usr/bin/env python3
"""Phase 0 — Repo Resolution (architecture §5.8.2).
Rules-first, and rules-ONLY: this emits resolution JSON, and below the
confidence threshold the answer is `needs_clarification` — pipeline.sh asks a
human on the ticket/PR and exits 0. That is the terminal answer, not a
placeholder.

This docstring used to promise an LLM fallback "invoked by pipeline.sh ... see
prompts/resolve-llm.md". Nothing invoked it, and the prompt has been removed.
ADR-5's step 2 is deliberately not built: a misroute is the one failure this
system cannot see (§5.15 — the run reports success, tests land in the wrong
repo, and coverage silently does not exist), so the layer that decides routing
is the last place to add a component that can be confidently wrong.

Usage:
  resolve.py pr   <source_repo> --changed-files files.txt
  resolve.py jira <KEY> --components "Checkout,Catalog" --labels "api-only" [--linked-repos "orders-api"]
"""
import argparse, fnmatch, json, pathlib, sys
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1] / "lib"))
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
    # A path relationship, never a string prefix. `f.startswith("openapi")`
    # fired for `openapi-backup/old.yaml` and `openapignore.txt` — a SIBLING
    # directory and an unrelated file that merely share the leading characters
    # — fanning consumer UI repos into a run that never touched the contract.
    # Same defect class as the bundle-import containment bug (review C2).
    cdir = contract.rsplit("/", 1)[0] + "/" if contract and "/" in contract else None
    if contract and any(f == contract or (cdir and f.startswith(cdir)) for f in changed):
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
    # INTERSECT the restrictions; last-one-wins silently discarded the others.
    # The shipped registry maps api-only -> [api] and ui-only -> [ui], so a
    # ticket carrying both routed to whichever label happened to come LAST --
    # measured: ['api-only','ui-only'] resolved to e2e-ui-tests-1 and the
    # reversed order to e2e-api-tests-1, both at confidence 0.85, i.e. the
    # platform was confident about a coin flip whose outcome depends on the
    # order JIRA happens to return labels in. The layer it dropped never got
    # tests generated, which is the unrouting this platform cannot see from
    # the inside.
    #
    # `restrict_layers` means "only these layers", so intersection is what the
    # word already promises. Contradictory labels intersect to EMPTY, which
    # yields no test repos, which caps confidence at 0.4 -- below the 0.8
    # threshold -- so the run asks a human instead of guessing. That is the
    # documented behaviour for "we cannot tell", reached without a special case.
    layers = None
    for l in labels:
        r = hints.get("jira_label_map", {}).get(l, {})
        if "restrict_layers" in r:
            rl = set(r["restrict_layers"])
            layers = rl if layers is None else (layers & rl)
    if layers is not None:
        layers = sorted(layers)
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
