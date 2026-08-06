#!/usr/bin/env python3
"""Structured authored + harvested facts for registered repositories.

Steps 1-2 of the proposal: the schema, the loader, and the HARVESTED tier
derived from what the estate already computes. The `observed` tier (flake,
churn, reviewer edits) is deliberately NOT here — it needs a real CI feed, and
shipping an empty tier that looks populated would be worse than not having it.

E2E test repositories retain their existing always-available facts behavior.
Application repositories opt in by adding ``knowledge/facts/<repo>.yaml``.  An
application repo without that authored file is deliberately invisible here, so
adopting B4 cannot alter an existing estate by accident.

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
import re
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


def app_repo_names(reg=None):
    try:
        from registry import load_registry
        reg = reg or load_registry()
        return [r["name"] for r in reg.get("source_repositories") or []]
    except Exception:
        return []


def is_test_repo(repo, reg=None):
    return repo in test_repo_names(reg)


def is_app_repo(repo, reg=None):
    return repo in app_repo_names(reg)


def app_opted_in(repo, reg=None):
    """True only when a registered application repo has an authored facts file."""
    return is_app_repo(repo, reg) and (FACTS_DIR / f"{repo}.yaml").is_file()


def facts_repo_names(reg=None):
    """Legacy test repos plus explicitly opted-in application repos."""
    try:
        from registry import load_registry
        reg = reg or load_registry()
    except Exception:
        return []
    tests = test_repo_names(reg)
    apps = [name for name in app_repo_names(reg) if app_opted_in(name, reg)]
    return tests + apps


def is_facts_repo(repo, reg=None):
    return is_test_repo(repo, reg) or app_opted_in(repo, reg)


def authored(repo):
    """What humans asserted. Tracked in git; never regenerated."""
    value = _read_yaml(FACTS_DIR / f"{repo}.yaml").get("authored") or {}
    return value if isinstance(value, dict) else {}


def harvested(repo):
    """What was derived from the repo. Rebuilt; hand edits are lost."""
    value = _read_yaml(DERIVED_DIR / f"{repo}.yaml").get("harvested") or {}
    return value if isinstance(value, dict) else {}


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
    doc = _read_yaml(FACTS_DIR / f"{repo}.yaml")
    declared = doc.get("repo")
    if declared is not None and declared != repo:
        problems.append(f"repo is {declared!r}, expected {repo!r}")
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

def _registry_entry(repo, reg):
    for entry in reg.get("source_repositories") or []:
        if entry.get("name") == repo:
            return entry, "app"
    for entry in reg.get("test_repositories") or []:
        if entry.get("name") == repo:
            return entry, "test"
    return None, None


def _catalog_rows():
    """(status, rows, source_count), keeping unavailable distinct from empty."""
    files = sorted(app_paths.catalog_files(ROOT))
    if not files:
        return "unavailable", [], 0
    rows, readable = [], 0
    for path in files:
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
            readable += 1
            for line in lines:
                if not line.strip():
                    continue
                try:
                    row = json.loads(line)
                except ValueError:
                    continue
                if isinstance(row, dict):
                    rows.append(row)
        except (OSError, UnicodeError):
            continue
    return ("available" if readable else "unavailable"), rows, readable


def _app_surface(entry):
    """Deterministic harvested surface with an explicit input state.

    ``available`` with an empty ``items`` list means the configured input was
    read and contained no recognized surface.  ``unavailable`` means the input
    was configured but no local checkout/fixture supplied it.  Callers must not
    collapse those two claims.
    """
    is_backend = entry.get("type") == "backend"
    field = "contract" if is_backend else "route_table"
    artifact = entry.get(field)
    kind = "endpoints" if is_backend else "routes"
    base = {"kind": kind, "input": field, "configured_path": artifact or ""}
    if not artifact:
        return {**base, "status": "not_configured", "source": "", "items": []}
    candidates = ((ROOT / "workspace/src" / entry["name"] / artifact,
                   f"workspace/src/{entry['name']}/{artifact}"),
                  (ROOT / "demo" / entry["name"] / artifact,
                   f"demo/{entry['name']}/{artifact}"))
    for path, source in candidates:
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        if is_backend:
            items = re.findall(r"^\s{2}(/[^:\s]+):", text, re.M)
        else:
            items = re.findall(r"path:\s*['\"]([^'\"]+)", text)
        return {**base, "status": "available", "source": source,
                "items": sorted(set(items))}
    return {**base, "status": "unavailable", "source": artifact, "items": []}


def _app_catalog(repo):
    status, rows, source_count = _catalog_rows()
    mapped, covered, counts = [], [], {}
    for row in rows:
        mapping = row.get("mapping") or {}
        if not isinstance(mapping, dict):
            continue
        if repo not in (mapping.get("app_repos") or []):
            continue
        state = str(mapping.get("status") or "unknown")
        counts[state] = counts.get(state, 0) + 1
        if state not in ("auto", "confirmed"):
            continue
        test_id = row.get("test_id")
        if test_id:
            mapped.append(str(test_id))
        evidence = row.get("evidence") or {}
        if not isinstance(evidence, dict):
            continue
        covered.extend(str(v) for v in (evidence.get("endpoints") or []))
        covered.extend(str(v) for v in (evidence.get("ui_routes") or []))
    return {"status": status, "source_count": source_count,
            "mapping_status_counts": {k: counts[k] for k in sorted(counts)},
            "mapped_tests": sorted(set(mapped)),
            "surface_covered": sorted(set(covered))}


def _build_app_harvested(entry, reg):
    name = entry["name"]
    covering = sorted(t["name"] for t in reg.get("test_repositories") or []
                      if name in (t.get("covers") or []))
    return {
        "repo_kind": "application",
        "scm": entry.get("scm") or "",
        "location": entry.get("url") or "",
        "type": entry.get("type") or "",
        "domains": sorted(set(entry.get("domains") or [])),
        "testable_paths": sorted(set(entry.get("testable_paths") or [])),
        "consumes_services": sorted(set(entry.get("consumes_services") or [])),
        "consumed_by": sorted(set(entry.get("consumed_by") or [])),
        "covering_test_repositories": covering,
        "surface": _app_surface(entry),
        "catalog": _app_catalog(name),
    }


def _build_test_harvested(entry):
    repo = entry["name"]
    out = {
        "generated_at": datetime.datetime.now(
            datetime.timezone.utc).isoformat(timespec="seconds"),
        "repo_kind": "test",
        "scm": entry.get("scm") or "",
        "location": entry.get("url") or "",
        "layer": entry.get("layer") or "",
        "framework": entry.get("framework") or "",
        "layout": entry.get("layout") or {},
        "covers": list(entry.get("covers") or []),
        "surface_covered": _surface_covered(repo),
    }
    try:
        import spec_exemplars
        prof = spec_exemplars.profile(
            ROOT / "workspace/tests" / repo, repo,
            (entry.get("layout") or {}).get("specs", ""))
        if isinstance(prof, dict):
            out["spec_count"] = prof.get("specs", 0)
            out["shared_helpers"] = sorted(p for p, _ in prof.get("helpers") or [])
    except Exception:
        pass
    return out

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
    entry, kind = _registry_entry(repo, reg)
    if entry is None or (kind == "app" and not app_opted_in(repo, reg)):
        return {}
    return _build_app_harvested(entry, reg) if kind == "app" \
        else _build_test_harvested(entry)


def rebuild(names=None, reg=None):
    """Regenerate the harvested tier. Returns the repos written."""
    try:
        from registry import load_registry
        reg = reg or load_registry()
    except Exception:
        return []
    targets = [n for n in (names or facts_repo_names(reg))
               if is_facts_repo(n, reg)]
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
        print(f"harvested facts rebuilt for {len(written)} repo(s): "
              f"{', '.join(written) or '-'}")
        return 0
    if cmd == "show" and len(argv) > 1:
        repo = argv[1]
        if not is_facts_repo(repo):
            print(f"{repo} is not an opted-in facts repository (application "
                  "repos opt in with knowledge/facts/<repo>.yaml)",
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
