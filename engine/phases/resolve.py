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
    # An EMPTY change list is not the same fact as a change list with nothing
    # testable in it, and the two were byte-identical here: both returned
    # `confidence 1.0, "no testable paths changed", skip`. One is an established
    # negative (the SCM listed 12 files and none match testable_paths); the
    # other is the platform having learned NOTHING about the PR -- an adapter
    # whose parse silently yielded nothing on a changed response shape, a token
    # without permission answering 200 with an empty array, pagination misread.
    # The pipeline aborts when `SCM changed_files` FAILS, so this is precisely
    # the case where it SUCCEEDS and says nothing (C13).
    #
    # It still skips: there is genuinely nothing to generate from either way,
    # and a legitimately empty PR (title-only edit, reverted commits) must not
    # start asking humans questions. What changes is the CLAIM -- confidence in
    # "this PR needs no tests" is zero when nothing was seen. `needs_clarification`
    # is computed as `confidence < threshold and not skip`, so this cannot alter
    # control flow; only the words a human reads.
    if not changed:
        return dict(source_repos=[repo_name], test_repos=[], cross_repo_impact=[],
                    confidence=0.0, skip=True, empty_change_list=True,
                    rationale="the SCM reported NO changed files for this PR, so "
                              "nothing was established about it - this is not a "
                              "finding that no testable path changed")
    testable = any(fnmatch.fnmatch(f, p) for f in changed for p in src.get("testable_paths", ["**"]))
    if not testable:
        return dict(source_repos=[repo_name], test_repos=[], cross_repo_impact=[],
                    confidence=1.0, skip=True, empty_change_list=False,
                    rationale=f"no testable paths changed ({len(changed)} file(s) "
                              f"examined)")
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
    # Same question on the PR path: a contract fan-out can pull in a consumer
    # that nothing covers, and the run would generate for the others and say
    # nothing about it. `layers` is not restricted here, so there is no
    # layer-filtered state to report -- an empty list, not a missing key, so
    # every consumer reads one shape.
    uncovered = sorted(s for s in sources if not test_repos_for(reg, s))
    return dict(source_repos=sources, test_repos=sorted(tests), cross_repo_impact=impact,
                confidence=1.0 if tests else 0.4,
                uncovered_sources=uncovered, layer_filtered_sources=[],
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
    # WHICH implicated repos will receive NOTHING, and why. Measured on the
    # shipped registry: a `Catalog` ticket resolves three source repos and ONE
    # test repo, because admin-portal-ui and catalog-api are covered by nothing
    # -- and the contract said only `test_repos: [e2e-ui-tests-1], confidence
    # 0.85`, so a reader reasonably concludes the ticket is covered. The
    # platform KNEW and did not say; `make coverage` warns at estate level,
    # which is not the moment this matters.
    #
    # Two states, not one, because the fixes differ (C13): a repo covered by
    # NOTHING needs a test repo onboarded or its `scope` extended, while a repo
    # dropped by a `restrict_layers` label was excluded ON PURPOSE and is
    # usually correct. Collapsing them would send someone to onboard a repo
    # that is already covered.
    uncovered, layer_filtered = [], []
    for s in sources:
        allowed = test_repos_for(reg, s, layers=layers)
        tests.update(allowed)
        if not allowed:
            (layer_filtered if test_repos_for(reg, s) else uncovered).append(s)
    conf = 0.95 if linked_repos else (0.85 if sources else 0.2)
    return dict(source_repos=sorted(sources), test_repos=sorted(tests), cross_repo_impact=[],
                confidence=conf if tests else min(conf, 0.4),
                uncovered_sources=sorted(uncovered),
                layer_filtered_sources=sorted(layer_filtered),
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
