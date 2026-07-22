#!/usr/bin/env python3
"""Repository administration — the library behind the dashboard's Repositories
view and bin/repos.py's add/scope/notes commands.

Manages the estate in registry/repo-registry.yaml:
  - app repos (ui -> frontend, service -> backend) and E2E test repos, add/edit
  - the many-to-one mapping: each test repo declares a hand-managed `scope`
    (app repos it is responsible for). `covers` stays GENERATED — regenerated
    as catalog evidence UNION scope by regen_coverage.py — so routing gains the
    declared mapping without hand-editing coverage.
  - per-repo agent guidance: team-authored knowledge/repos/<name>.md plus any
    AGENTS.md / CLAUDE.md found inside the repo's checkout (workspace/ first,
    demo/ fallback). Both are merged into the estate AGENTS.md by
    bin/gen_agents_md.py and therefore reach every LLM phase (test plans,
    generation, coverage-gap fixes).

Every mutation re-validates references, re-runs the routing goldens (skipped
inside pytest — same guard as bin/repos.py) and regenerates AGENTS.md.
"""
import functools, os, pathlib, re, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import yaml
import fs_lock
from registry import load_registry


def _locked(fn):
    """Registry mutations are read-modify-write over a committable file, shared by
    the dashboard server and the CLI — serialize them (see fs_lock)."""
    @functools.wraps(fn)
    def wrap(*a, **k):
        with fs_lock.lock(REG_PATH):
            return fn(*a, **k)
    return wrap

REG_PATH = ROOT / "registry/repo-registry.yaml"
NOTES_DIR = ROOT / "knowledge/repos"
NAME_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{1,63}$")
SCM_KINDS = ("github", "bitbucket", "stash")
APP_KINDS = {"ui": "frontend", "frontend": "frontend",
             "service": "backend", "backend": "backend"}
KIND_LABEL = {"frontend": "ui", "backend": "service"}
LAYERS = ("api", "ui")
GUIDANCE_FILES = ("AGENTS.md", "CLAUDE.md")
GUIDANCE_MAX = 4000                       # chars per source merged into AGENTS.md


def _fail(msg):
    raise SystemExit(msg)


def _csv(v):
    if v is None:
        return None
    if isinstance(v, str):
        return [x.strip() for x in v.split(",") if x.strip()]
    return list(v)


def _entry(reg, name, sect):
    return next((r for r in reg[sect] if r["name"] == name), None)


def save_and_verify(reg, regen_cov=False):
    # Atomic write: never leave a half-dumped registry behind on crash
    tmp = REG_PATH.with_suffix(".yaml.tmp")
    tmp.write_text(yaml.safe_dump(reg, sort_keys=False), encoding="utf-8")
    os.replace(tmp, REG_PATH)
    if regen_cov:                          # covers = catalog evidence UNION scope
        subprocess.run([sys.executable, "catalog/bootstrap/regen_coverage.py"],
                       cwd=ROOT, check=True, capture_output=True,
                       stdin=subprocess.DEVNULL)
    if "PYTEST_CURRENT_TEST" not in os.environ:   # never pytest-from-pytest
        r = subprocess.run([sys.executable, "-m", "pytest",
                            "registry/tests/test_routing_golden.py", "-q"],
                           cwd=ROOT, capture_output=True, text=True,
                           stdin=subprocess.DEVNULL)
        if r.returncode != 0:
            print("WARNING: registry change broke routing goldens - review before committing")
    subprocess.run([sys.executable, str(ROOT / "bin/gen_agents_md.py")], cwd=ROOT,
                   check=True, capture_output=True, stdin=subprocess.DEVNULL)


# ---------------------------------------------------------------- summaries

def summary():
    reg = load_registry()
    apps, tests = [], []
    for r in reg["source_repositories"]:
        apps.append({**r, "kind": KIND_LABEL[r["type"]],
                     "covered_by": sorted(t["name"] for t in reg["test_repositories"]
                                          if r["name"] in t.get("covers", [])),
                     "has_notes": (NOTES_DIR / f"{r['name']}.md").exists(),
                     "local_files": [f["path"] for f in repo_local_files(r["name"])]})
    for t in reg["test_repositories"]:
        tests.append({**t, "scope": t.get("scope", []),
                      "has_notes": (NOTES_DIR / f"{t['name']}.md").exists(),
                      "local_files": [f["path"] for f in repo_local_files(t["name"])]})
    return {"app_repos": apps, "test_repos": tests}


# ---------------------------------------------------------------- app repos

@_locked
def upsert_app(name, kind=None, scm=None, url=None, domains=None,
               testable_paths=None, contract=None, route_table=None,
               consumes_services=None):
    """Add or edit an application repository. `kind` accepts ui|service (or the
    registry's frontend|backend). Only provided fields change on edit."""
    if not NAME_RE.fullmatch(name or ""):
        _fail(f"invalid repo name: {name!r} (lowercase slug, 2-64 chars)")
    reg = load_registry()
    if _entry(reg, name, "test_repositories"):
        _fail(f"{name} is already a test repository")
    r = _entry(reg, name, "source_repositories")
    creating = r is None
    if creating:
        if not kind or not url:
            _fail("adding an app repo requires kind (ui|service) and url")
        r = {"name": name, "domains": [], "testable_paths": ["src/**"]}
        reg["source_repositories"].append(r)
    if kind is not None:
        if kind.lower() not in APP_KINDS:
            _fail("kind must be ui|service (frontend|backend)")
        r["type"] = APP_KINDS[kind.lower()]
    if scm is not None:
        if scm not in SCM_KINDS:
            _fail(f"scm must be one of: {', '.join(SCM_KINDS)}")
        r["scm"] = scm
    elif creating:
        r["scm"] = "bitbucket"
    if url is not None:
        r["url"] = url
    for field, val in (("domains", _csv(domains)),
                       ("testable_paths", _csv(testable_paths))):
        if val is not None:
            r[field] = val
    if contract is not None:
        r["contract"] = contract
    if route_table is not None:
        r["route_table"] = route_table
    if consumes_services is not None:
        wanted = _csv(consumes_services)
        backends = {x["name"] for x in reg["source_repositories"]
                    if x.get("type") == "backend" and x["name"] != name}
        unknown = sorted(set(wanted) - backends)
        if unknown:
            _fail(f"unknown service repo(s): {', '.join(unknown)}")
        r["consumes_services"] = sorted(wanted)
        for b in reg["source_repositories"]:
            if b.get("type") != "backend":
                continue
            consumed = set(b.get("consumed_by", []))
            (consumed.add if b["name"] in wanted else consumed.discard)(name)
            b["consumed_by"] = sorted(consumed)
    save_and_verify(reg)
    return {"name": name, "created": creating}


@_locked
def remove_app(name, force=False):
    reg = load_registry()
    if not _entry(reg, name, "source_repositories"):
        _fail(f"not registered: {name}")
    covered = [t["name"] for t in reg["test_repositories"]
               if name in t.get("covers", [])]
    if covered and not force:
        _fail(f"{name} is still covered by {', '.join(covered)} — remap those "
              f"tests (or its scope) first, or pass force")
    reg["source_repositories"] = [r for r in reg["source_repositories"]
                                  if r["name"] != name]
    for r in reg["source_repositories"]:
        for field in ("consumed_by", "consumes_services"):
            if name in r.get(field, []):
                r[field].remove(name)
    for t in reg["test_repositories"]:
        if name in t.get("scope", []):
            t["scope"].remove(name)
    save_and_verify(reg, regen_cov=True)
    return {"name": name, "removed": True}


# ---------------------------------------------------------------- test repos

@_locked
def upsert_test(name, layer=None, framework=None, scm=None, url=None,
                specs=None, fixtures=None, scope=None):
    """Add or edit an E2E test repository. `scope` declares the app repos this
    repo is responsible for (many app repos -> one test repo)."""
    if not NAME_RE.fullmatch(name or ""):
        _fail(f"invalid repo name: {name!r} (lowercase slug, 2-64 chars)")
    reg = load_registry()
    if _entry(reg, name, "source_repositories"):
        _fail(f"{name} is already an application repository")
    t = _entry(reg, name, "test_repositories")
    creating = t is None
    if creating:
        if not layer or not framework or not url:
            _fail("adding a test repo requires layer (api|ui), framework and url")
        t = {"name": name, "layout": {"specs": "tests/"}, "covers": []}
        reg["test_repositories"].append(t)
    if layer is not None:
        if layer not in LAYERS:
            _fail("layer must be api|ui")
        t["layer"] = layer
    if framework is not None:
        t["framework"] = framework
    if scm is not None:
        if scm not in SCM_KINDS:
            _fail(f"scm must be one of: {', '.join(SCM_KINDS)}")
        t["scm"] = scm
    elif creating:
        t["scm"] = "bitbucket"
    if url is not None:
        t["url"] = url
    if specs is not None:
        t.setdefault("layout", {})["specs"] = specs
    if fixtures is not None:
        t.setdefault("layout", {})["fixtures"] = fixtures
    if scope is not None:
        t["scope"] = _valid_scope(reg, _csv(scope))
    save_and_verify(reg, regen_cov=True)
    return {"name": name, "created": creating}


def _valid_scope(reg, apps):
    known = {r["name"] for r in reg["source_repositories"]}
    unknown = sorted(set(apps) - known)
    if unknown:
        _fail(f"unknown app repo(s): {', '.join(unknown)}")
    return sorted(set(apps))


@_locked
def set_scope(test_repo, apps):
    """Declare which app repos `test_repo` is responsible for. covers is then
    regenerated as catalog evidence UNION this scope."""
    reg = load_registry()
    t = _entry(reg, test_repo, "test_repositories")
    if not t:
        _fail(f"not a test repository: {test_repo}")
    t["scope"] = _valid_scope(reg, _csv(apps) or [])
    save_and_verify(reg, regen_cov=True)
    return {"test_repo": test_repo, "scope": t["scope"]}


@_locked
def remove_test(name, force=False):
    import glob, json
    reg = load_registry()
    if not _entry(reg, name, "test_repositories"):
        _fail(f"not registered: {name}")
    cataloged = sum(1 for f in glob.glob(str(ROOT / "catalog/*.jsonl"))
                    if pathlib.Path(f).name != "catalog.sample.jsonl"
                    for l in open(f, encoding="utf-8") if l.strip()
                    and json.loads(l)["test_repo"] == name)
    if cataloged and not force:
        _fail(f"{name} still has {cataloged} cataloged test(s) — retire or remap "
              f"them first, or pass force")
    reg["test_repositories"] = [t for t in reg["test_repositories"]
                                if t["name"] != name]
    save_and_verify(reg)
    return {"name": name, "removed": True}


# ---------------------------------------------------------------- guidance

def _known(name):
    reg = load_registry()
    if not (_entry(reg, name, "source_repositories")
            or _entry(reg, name, "test_repositories")):
        _fail(f"not a registered repo: {name}")
    return name


def repo_local_files(name):
    """AGENTS.md / CLAUDE.md belonging to the repo itself.

    Sources, in order of authority: the workspace clone (the exact revision under
    test) and the knowledge/synced/ cache (last explicit pull from the SCM), with
    demo/ as a last-resort fixture fallback.

    Between the clone and the cache we take the FRESHER one rather than a fixed
    winner: during a run the clone is newly made so it wins, while after a manual
    sync the cache is newer so it wins. A fixed clone-wins rule let a leftover
    clone from an earlier run silently shadow guidance the user had just synced."""
    out, seen = [], set()
    synced = {pathlib.Path(f["path"]).name: f for f in _synced_files(name)}
    for fname in GUIDANCE_FILES:
        clone = next((b / fname for b in (ROOT / "workspace/src" / name,
                                          ROOT / "workspace/tests" / name)
                      if (b / fname).exists()), None)
        cached = synced.get(fname)
        pick = None
        if clone and cached:
            cached_at = (ROOT / cached["path"]).stat().st_mtime \
                if (ROOT / cached["path"]).exists() else 0
            pick = ("clone", clone) if clone.stat().st_mtime >= cached_at \
                else ("cache", cached)
        elif clone:
            pick = ("clone", clone)
        elif cached:
            pick = ("cache", cached)
        if pick is None:                              # demo fixture fallback
            p = ROOT / "demo" / name / fname
            if p.exists():
                pick = ("clone", p)
        if pick is None:
            continue
        seen.add(fname)
        if pick[0] == "cache":
            out.append(pick[1])
        else:
            out.append({"path": pick[1].relative_to(ROOT).as_posix(),
                        "text": pick[1].read_text(encoding="utf-8", errors="ignore")})
    return out


def _synced_files(name):
    try:
        import guidance_sync
        return guidance_sync.synced_files(name)
    except Exception:                                 # sync cache is optional
        return []


def get_notes(name):
    _known(name)
    p = NOTES_DIR / f"{name}.md"
    return {"repo": name,
            "team": p.read_text(encoding="utf-8") if p.exists() else "",
            "path": p.relative_to(ROOT).as_posix(),
            "local_files": [{"path": f["path"], "chars": len(f["text"])}
                            for f in repo_local_files(name)]}


def set_notes(name, text):
    """Write (or clear, when empty) the team guidance for a repo, then refresh
    AGENTS.md so the next generation run sees it."""
    _known(name)
    p = NOTES_DIR / f"{name}.md"
    if text.strip():
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text.rstrip() + "\n", encoding="utf-8", newline="\n")
    else:
        p.unlink(missing_ok=True)
    subprocess.run([sys.executable, str(ROOT / "bin/gen_agents_md.py")], cwd=ROOT,
                   check=True, capture_output=True, stdin=subprocess.DEVNULL)
    return {"repo": name, "saved": bool(text.strip()),
            "path": p.relative_to(ROOT).as_posix()}


def guidance_for(name):
    """All guidance sources for a repo, merged for AGENTS.md generation:
    [(source_label, text)] — team notes first, then repo-local files."""
    out = []
    p = NOTES_DIR / f"{name}.md"
    if p.exists():
        out.append((f"team notes ({p.relative_to(ROOT).as_posix()})",
                    p.read_text(encoding="utf-8")))
    for f in repo_local_files(name):
        out.append((f["path"], f["text"]))
    return [(src, t[:GUIDANCE_MAX] + ("\n… (truncated)" if len(t) > GUIDANCE_MAX
                                      else "")) for src, t in out]
