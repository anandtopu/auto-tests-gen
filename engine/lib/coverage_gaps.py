#!/usr/bin/env python3
"""Coverage-gap analysis: compare each app repo's harvested surface (OpenAPI
endpoints, frontend routes) against the Test Catalog's evidence and report what
has NO test exercising it. Deterministic — this is what "fill the coverage gaps"
means in the platform's evidence model (line-level instrumentation is an
estate-specific add-on; see docs).

Consumers: bin/qa.py gaps, the pipeline (out/coverage-gaps.md phase context),
and bin/gen_agents_md.py (annotates uncovered surface in AGENTS.md).
"""
import json, pathlib, re, sys
import app_paths

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
from registry import load_registry


def norm(path):
    """/v1/orders/123 -> /v1/orders/{id} (same normalization as correlate.py)."""
    return re.sub(r"/\d+", "/{id}", path)


OBSERVED = ("harvested", "empty")


def harvest(repo):
    """(surface, status, detail) — FOUR outcomes, and three of them are not
    "this repo has no gaps":

      harvested  - artifact declared, found, surface extracted.
      empty      - artifact found and READ, nothing matched. Either it declares
                   no surface or its shape is not one this regex proxy knows;
                   both are stated, neither is silence.
      unreadable - artifact DECLARED but absent here. We could not look.
      undeclared - no contract/route_table registered. Nothing to look at.

    The last two are C13 territory. compute() used to `continue` on all three
    non-harvested outcomes, so a repo we could not observe left the report
    entirely and read as a repo with nothing to fix — while the SAME estate's
    AGENTS.md said "contract `openapi/payments.yaml` not available locally".
    bin/gen_agents_md.py has always rendered this case honestly; the shared
    library three other surfaces read did not.
    """
    backend = repo["type"] == "backend"
    art = repo.get("contract") if backend else repo.get("route_table")
    kind = "contract" if backend else "route table"
    if not art:
        return [], "undeclared", (
            f"no {kind} is registered, so this repo's surface has never been "
            f"looked at — register one with bin/repos.py")
    for base in (ROOT / "workspace/src" / repo["name"], ROOT / "demo" / repo["name"]):
        p = base / art
        if p.exists():
            text = p.read_text(encoding="utf-8", errors="ignore")
            surface = (sorted(set(re.findall(r"^\s{2}(/[^:\s]+):", text, re.M)))
                       if backend else
                       sorted(set(re.findall(r"path:\s*['\"]([^'\"]+)", text))))
            if surface:
                return surface, "harvested", ""
            return [], "empty", (
                f"{kind} `{art}` was read but declares no surface this "
                f"extractor recognizes")
    return [], "unreadable", (
        f"{kind} `{art}` is not available locally (it appears under "
        f"workspace/src/{repo['name']}/ during a run) — this repo's surface "
        f"was NOT checked")


def observed(entry):
    """Did we actually look? False means every count derived from this entry is
    UNKNOWN, not zero — callers must not sum it into a total as a 0."""
    return entry.get("status", "harvested") in OBSERVED


def harvest_surface(repo):
    """Endpoints (backend) or routes (frontend) from the freshest clone."""
    return harvest(repo)[0]


def catalog_evidence():
    """All normalized endpoints/routes exercised by confirmed/auto-mapped tests,
    grouped per app repo."""
    per_repo = {}
    for f in app_paths.catalog_files(ROOT):
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


# Risk weighting (roadmap 3.2). Deterministic ON PURPOSE: which gap matters most is
# a judgement rules make better and reproducibly than a model — a payments POST
# outranks a static GET every time, and the ranking must not wobble between runs.
_MUTATING = ("post ", "put ", "patch ", "delete ")
_SENSITIVE = ("auth", "login", "token", "password", "payment", "checkout",
              "admin", "user", "account")


def risk_score(surface):
    """(score, reasons) for one uncovered surface string (endpoint or route)."""
    s = surface.lower()
    score, reasons = 1, []
    if any(s.startswith(m) or f" {m}" in s for m in _MUTATING):
        score += 2
        reasons.append("mutating")
    if any(tok in s for tok in _SENSITIVE):
        score += 2
        reasons.append("sensitive")
    if "{" in s or ":" in s.split(" ")[-1]:
        score += 1
        reasons.append("addresses-state")
    return score, reasons


def compute(only_repo=None):
    reg = load_registry()
    evidence = catalog_evidence()
    out = {}
    for r in reg["source_repositories"]:
        if only_repo and r["name"] != only_repo:
            continue
        surface, status, detail = harvest(r)
        exercised = evidence.get(r["name"], set())
        covered = [s for s in surface if norm(s) in exercised]
        uncovered = [s for s in surface if norm(s) not in exercised]
        # Additive: existing consumers keep reading `uncovered` untouched; ranked
        # view is a new field, highest risk first (ties keep surface order).
        ranked = sorted(
            ({"surface": s, "score": risk_score(s)[0], "reasons": risk_score(s)[1]}
             for s in uncovered), key=lambda g: -g["score"])
        out[r["name"]] = {"kind": "endpoints" if r["type"] == "backend" else "routes",
                          "surface": surface, "covered": covered,
                          "uncovered": uncovered, "uncovered_ranked": ranked,
                          "status": status, "detail": detail}
    return out


def to_markdown(only_repo=None):
    gaps = compute(only_repo)
    seen = {n: g for n, g in gaps.items() if observed(g)}
    blind = {n: g for n, g in gaps.items() if not observed(g)}
    lines = ["# Coverage gaps (harvested surface vs Test Catalog evidence)", ""]
    if not seen:
        lines.append("No harvestable surface found (contracts/route tables unavailable).")
    for name, g in seen.items():
        lines.append(f"## {name} ({g['kind']})")
        if g["status"] == "empty":
            lines.append(f"- (none) {g['detail']}")
        for s in g["covered"]:
            lines.append(f"- [covered] {s}")
        # Highest risk first, with the reasons on the line — generation and the plan
        # adversary read this file, so the ordering nudges what gets covered first.
        for item in g.get("uncovered_ranked") or [
                {"surface": s, "score": 1, "reasons": []} for s in g["uncovered"]]:
            why = f" ({', '.join(item['reasons'])})" if item["reasons"] else ""
            lines.append(f"- [NO TEST] (risk {item['score']}){why} {item['surface']}"
                         f"  <- coverage gap: prioritize a scenario here")
        lines.append("")
    if blind:
        # NOT a gap list. An absent section used to be indistinguishable from a
        # clean one, so a repo nobody could look at read as a repo with nothing
        # to fix — in a file that is injected as context into every authoring
        # phase, which then never hears the repo exists.
        lines += ["## Repos whose surface was NOT checked", "",
                  "These are **not** known to be gap-free — nothing below was "
                  "examined, so no conclusion about their coverage is available "
                  "from this report.", ""]
        for name, g in blind.items():
            lines.append(f"- **{name}**: {g['detail']}")
        lines.append("")
    return "\n".join(lines)


if __name__ == "__main__":
    fmt = sys.argv[1] if len(sys.argv) > 1 else "md"
    repo = sys.argv[2] if len(sys.argv) > 2 else None
    if fmt == "json":
        print(json.dumps(compute(repo), indent=2))
    else:
        print(to_markdown(repo))
