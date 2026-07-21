#!/usr/bin/env python3
"""Coverage-gap analysis: compare each app repo's harvested surface (OpenAPI
endpoints, frontend routes) against the Test Catalog's evidence and report what
has NO test exercising it. Deterministic — this is what "fill the coverage gaps"
means in the platform's evidence model (line-level instrumentation is an
estate-specific add-on; see docs).

Consumers: bin/qa.py gaps, the pipeline (out/coverage-gaps.md phase context),
and bin/gen_agents_md.py (annotates uncovered surface in AGENTS.md).
"""
import glob, json, pathlib, re, sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
from registry import load_registry


def norm(path):
    """/v1/orders/123 -> /v1/orders/{id} (same normalization as correlate.py)."""
    return re.sub(r"/\d+", "/{id}", path)


def harvest_surface(repo):
    """Endpoints (backend) or routes (frontend) from the freshest clone."""
    art = repo.get("contract") if repo["type"] == "backend" else repo.get("route_table")
    if not art:
        return []
    for base in (ROOT / "workspace/src" / repo["name"], ROOT / "demo" / repo["name"]):
        p = base / art
        if p.exists():
            text = p.read_text(encoding="utf-8", errors="ignore")
            if repo["type"] == "backend":
                return sorted(set(re.findall(r"^\s{2}(/[^:\s]+):", text, re.M)))
            return sorted(set(re.findall(r"path:\s*['\"]([^'\"]+)", text)))
    return []


def catalog_evidence():
    """All normalized endpoints/routes exercised by confirmed/auto-mapped tests,
    grouped per app repo."""
    per_repo = {}
    for f in sorted(glob.glob(str(ROOT / "catalog/*.jsonl"))):
        if pathlib.Path(f).name == "catalog.sample.jsonl":
            continue
        for line in open(f, encoding="utf-8"):
            if not line.strip():
                continue
            e = json.loads(line)
            if e["mapping"]["status"] not in ("confirmed", "auto"):
                continue
            eps = {norm(x.split(" ", 1)[-1]) for x in e["evidence"].get("endpoints", [])}
            rts = {norm(x) for x in e["evidence"].get("ui_routes", [])}
            for app in e["mapping"]["app_repos"]:
                per_repo.setdefault(app, set()).update(eps | rts)
    return per_repo


def compute(only_repo=None):
    reg = load_registry()
    evidence = catalog_evidence()
    out = {}
    for r in reg["source_repositories"]:
        if only_repo and r["name"] != only_repo:
            continue
        surface = harvest_surface(r)
        if not surface:
            continue
        exercised = evidence.get(r["name"], set())
        covered = [s for s in surface if norm(s) in exercised]
        uncovered = [s for s in surface if norm(s) not in exercised]
        out[r["name"]] = {"kind": "endpoints" if r["type"] == "backend" else "routes",
                          "surface": surface, "covered": covered, "uncovered": uncovered}
    return out


def to_markdown(only_repo=None):
    gaps = compute(only_repo)
    lines = ["# Coverage gaps (harvested surface vs Test Catalog evidence)", ""]
    if not gaps:
        lines.append("No harvestable surface found (contracts/route tables unavailable).")
    for name, g in gaps.items():
        lines.append(f"## {name} ({g['kind']})")
        for s in g["covered"]:
            lines.append(f"- [covered] {s}")
        for s in g["uncovered"]:
            lines.append(f"- [NO TEST] {s}  <- coverage gap: prioritize a scenario here")
        lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    fmt = sys.argv[1] if len(sys.argv) > 1 else "md"
    repo = sys.argv[2] if len(sys.argv) > 2 else None
    if fmt == "json":
        print(json.dumps(compute(repo), indent=2))
    else:
        print(to_markdown(repo))
