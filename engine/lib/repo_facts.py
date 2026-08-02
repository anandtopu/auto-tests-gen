#!/usr/bin/env python3
"""Structured per-repo facts for E2E TEST repos (docs/knowledge-base-proposal.md).

Steps 1-2 of the proposal: the schema, the loader, and the HARVESTED tier
derived from what the estate already computes. The `observed` tier (flake,
churn, reviewer edits) is deliberately NOT here — it needs a real CI feed, and
shipping an empty tier that looks populated would be worse than not having it.

Scope: E2E test repos only. An app repo's useful facts are surface + ownership,
which the registry and the harvested contract already carry; conventions and
pitfalls are test-repo concepts, and modelling both identically would be a
false symmetry.

TWO FILES, not one — a deviation from the proposal, for a reason this codebase
has felt repeatedly:

    knowledge/facts/<repo>.yaml           AUTHORED. Tracked. Never regenerated.
    knowledge/facts/derived/<repo>.yaml   HARVESTED. Gitignored. Rebuilt freely.

The proposal drew all tiers in one document, which reads better but dirties git
on every rebuild — the same churn that makes re-parking the estate a chore. A
human's assertions and a generator's output do not belong in the same file.

PRECEDENCE (constitution C6 extended): repo_owned > authored > harvested.
Nothing generated ever outranks something a human wrote, so `merged()` lets an
authored key win and records which tier answered.

ABSENCE IS NORMAL. No facts file means no facts — every accessor returns empty
and every caller behaves exactly as it did before this module existed.

CLI:
  repo_facts.py show <repo>        merged view with provenance
  repo_facts.py rebuild [repo...]  regenerate the harvested tier
"""
import datetime
import json
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import app_paths                      # R12: mutable paths resolve here

ROOT = pathlib.Path(__file__).resolve().parents[2]
FACTS_DIR = app_paths.knowledge_dir("facts", ROOT)
DERIVED_DIR = FACTS_DIR / "derived"
SCHEMA = 1

# Severity vocabulary for authored conventions/pitfalls. `must` is the one that
# earns MUST-KEEP treatment in retrieval later (step 5); the rest compete for
# whatever context budget remains.
SEVERITIES = ("must", "should", "avoid")


def _yaml():
    import yaml
    return yaml


def _read_yaml(path):
    """Parsed mapping, or {} — a missing or damaged facts file must never break
    a run. Facts are an ENRICHMENT; the pipeline worked without them before."""
    p = pathlib.Path(path)
    if not p.exists():
        return {}
    try:
        data = _yaml().safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception as e:
        print(f"[repo-facts] {p.name} is unreadable ({e}) — continuing without "
              f"it", file=sys.stderr)
        return {}
    if not isinstance(data, dict):
        return {}
    got = data.get("schema")
    if got is not None and got != SCHEMA:
        print(f"[repo-facts] {p.name} is schema {got}, this build speaks "
              f"{SCHEMA} — ignoring it rather than guessing at its shape",
              file=sys.stderr)
        return {}
    return data


def test_repo_names(reg=None):
    try:
        from registry import load_registry
        reg = reg or load_registry()
        return [t["name"] for t in reg.get("test_repositories") or []]
    except Exception:
        return []


def is_test_repo(repo, reg=None):
    return repo in test_repo_names(reg)


def authored(repo):
    """What humans asserted. Tracked in git; never regenerated."""
    return _read_yaml(FACTS_DIR / f"{repo}.yaml").get("authored") or {}


def harvested(repo):
    """What was derived from the repo. Rebuilt; hand edits are lost."""
    return _read_yaml(DERIVED_DIR / f"{repo}.yaml").get("harvested") or {}


def merged(repo):
    """Both tiers with provenance, authored winning any shared key.

    Returns {"repo", "tiers": {key: tier}, "authored": {...}, "harvested": {...}}
    so a caller can say "the team asserts X" vs "harvested from the repo",
    instead of flattening the difference away.
    """
    a, h = authored(repo), harvested(repo)
    tiers = {k: "harvested" for k in h}
    tiers.update({k: "authored" for k in a})
    return {"repo": repo, "authored": a, "harvested": h, "tiers": tiers}


def conventions(repo, severity=None):
    """Authored conventions, optionally filtered to one severity."""
    rows = [c for c in (authored(repo).get("conventions") or [])
            if isinstance(c, dict)]
    if severity:
        rows = [c for c in rows if c.get("severity") == severity]
    return rows


def validate(repo):
    """Problems with a repo's AUTHORED file, as a list of strings.

    Only the authored tier is validated: the derived tier is this module's own
    output, and validating your own output tells you nothing.
    """
    problems = []
    a = authored(repo)
    for field in ("conventions", "pitfalls"):
        for i, row in enumerate(a.get(field) or []):
            if not isinstance(row, dict):
                problems.append(f"{field}[{i}] is not a mapping")
                continue
            sev = row.get("severity")
            if sev is not None and sev not in SEVERITIES:
                problems.append(
                    f"{field}[{i}] severity {sev!r} is not one of "
                    f"{'/'.join(SEVERITIES)}")
            if field == "conventions" and not row.get("rule"):
                problems.append(f"conventions[{i}] has no `rule`")
    return problems


# ------------------------------------------------------------ harvested tier

def _surface_covered(repo):
    """Endpoints / UI routes this repo's cataloged tests actually exercise —
    read from the catalog rather than re-derived, so it cannot disagree with
    the evidence the gate and the coverage report already use."""
    seen = []
    for f in sorted(app_paths.catalog_dir(ROOT).glob("*.jsonl")):
        if f.name == "catalog.sample.jsonl":
            continue
        try:
            for line in f.open(encoding="utf-8"):
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if row.get("test_repo") != repo:
                    continue
                ev = row.get("evidence") or {}
                for item in (ev.get("endpoints") or []) + (ev.get("ui_routes") or []):
                    if item not in seen:
                        seen.append(item)
        except OSError:
            continue
    return seen


def build_harvested(repo, reg=None):
    """The harvested tier for one repo, from what the estate already computes.

    Deliberately a RESHAPING of existing output — registry entry, catalog
    evidence, and the exemplar profiler's layout scan. No new analysis, so this
    cannot drift away from what the phases already see.
    """
    try:
        from registry import load_registry
        reg = reg or load_registry()
    except Exception:
        return {}
    entry = next((t for t in reg.get("test_repositories") or []
                  if t["name"] == repo), None)
    if entry is None:
        return {}
    out = {
        "generated_at": datetime.datetime.now(
            datetime.timezone.utc).isoformat(timespec="seconds"),
        "layer": entry.get("layer") or "",
        "framework": entry.get("framework") or "",
        "layout": entry.get("layout") or {},
        "covers": list(entry.get("covers") or []),
        "surface_covered": _surface_covered(repo),
    }
    # Shared helpers: PATHS only. The exemplar profiler already ships the bodies
    # to the phases that need them; duplicating the text here would give two
    # sources for the same thing and let them disagree.
    try:
        import spec_exemplars
        prof = spec_exemplars.profile(
            ROOT / "workspace/tests" / repo, repo,
            (entry.get("layout") or {}).get("specs", ""))
        if isinstance(prof, dict):
            out["spec_count"] = prof.get("specs", 0)
            out["shared_helpers"] = [p for p, _ in prof.get("helpers") or []]
    except Exception:
        pass                      # a repo that is not checked out simply has less
    return out


def rebuild(names=None, reg=None):
    """Regenerate the harvested tier. Returns the repos written."""
    try:
        from registry import load_registry
        reg = reg or load_registry()
    except Exception:
        return []
    targets = [n for n in (names or test_repo_names(reg))
               if is_test_repo(n, reg)]
    DERIVED_DIR.mkdir(parents=True, exist_ok=True)
    written = []
    for repo in targets:
        h = build_harvested(repo, reg)
        if not h:
            continue
        doc = {"repo": repo, "schema": SCHEMA, "harvested": h}
        (DERIVED_DIR / f"{repo}.yaml").write_text(
            _yaml().safe_dump(doc, sort_keys=False), encoding="utf-8",
            newline="\n")
        written.append(repo)
    return written


def main(argv):
    sys.stdout.reconfigure(encoding="utf-8")
    sys.stderr.reconfigure(encoding="utf-8")
    cmd = argv[0] if argv else "show"
    if cmd == "rebuild":
        written = rebuild(argv[1:] or None)
        print(f"harvested facts rebuilt for {len(written)} test repo(s): "
              f"{', '.join(written) or '-'}")
        return 0
    if cmd == "show" and len(argv) > 1:
        repo = argv[1]
        if not is_test_repo(repo):
            print(f"{repo} is not a registered E2E test repo — facts are a "
                  f"test-repo concept (see docs/knowledge-base-proposal.md)",
                  file=sys.stderr)
            return 1
        m = merged(repo)
        problems = validate(repo)
        print(json.dumps(m, indent=2))
        for p in problems:
            print(f"[repo-facts] {repo}: {p}", file=sys.stderr)
        return 0
    print(__doc__, file=sys.stderr)
    return 64


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
