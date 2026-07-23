"""Generated per-repo AGENTS.md (engine/lib/repo_guidance_gen.py).

Two properties carry the feature and both are easy to break silently:

  1. A generated file must NEVER outrank guidance the repo actually owns. If it did,
     a team could commit an AGENTS.md and have it quietly ignored.
  2. What we generate must actually REACH the phases — via guidance_for() into the
     estate AGENTS.md — otherwise adding a repo still teaches the agent nothing.
"""
import ast, pathlib, subprocess, sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
import repo_admin
import repo_guidance_gen as rgg


@pytest.fixture
def gen_dir(tmp_path, monkeypatch):
    """Write generated guidance to a throwaway dir, never the real repo."""
    d = tmp_path / "generated"
    monkeypatch.setattr(rgg, "GEN_DIR", d)
    return d


def _reg():
    from registry import load_registry
    return load_registry()


def _an_app():
    return _reg()["source_repositories"][0]["name"]


def _a_test_repo():
    return _reg()["test_repositories"][0]["name"]


def _clean_repo():
    """A registered repo with NO guidance of its own — the case this feature exists
    for. Several demo repos ship an AGENTS.md/CLAUDE.md, so picking [0] blindly gives
    a repo where `skipped_has_own` is the correct answer, not a bug."""
    reg = _reg()
    names = [r["name"] for r in reg["source_repositories"]] + \
            [t["name"] for t in reg["test_repositories"]]
    for n in names:
        if not rgg.has_real_guidance(n):
            return n
    pytest.skip("every registered repo ships its own guidance")


# ------------------------------------------------------------------- rendering

def test_app_repo_render_carries_the_facts_a_test_author_needs():
    reg = _reg()
    app = next(r for r in reg["source_repositories"] if r.get("contract"))
    text = rgg.render(app["name"], reg)
    assert text.startswith(f"# AGENTS.md — {app['name']}")
    assert rgg.MARKER in text, "generated files must be self-identifying"
    assert "application repository" in text
    for d in app.get("domains", [])[:1]:
        assert d in text, "business domains missing"
    assert "Never modify application source" in text
    assert "catalog sidecar" in text, "born-mapped rule must be stated"


def test_test_repo_render_states_layout_scope_and_gate_rules():
    reg = _reg()
    t = reg["test_repositories"][0]
    text = rgg.render(t["name"], reg)
    assert "end-to-end test repository" in text
    assert t.get("framework", "") in text
    for v in (t.get("layout") or {}).values():
        assert v in text, f"layout dir {v} not documented"
    assert "exit 4" in text and "exit 2" in text, "gate exit codes not stated"
    assert "only component that commits or pushes" in text


def test_test_repo_conventions_are_single_sourced_not_forked():
    """The generator must quote skills/, never restate conventions in its own words."""
    reg = _reg()
    t = next(x for x in reg["test_repositories"] if x.get("layer"))
    layer = t["layer"]
    if not (ROOT / f"skills/e2e-{layer}-conventions/SKILL.md").exists():
        pytest.skip(f"no conventions file for layer {layer}")
    text = rgg.render(t["name"], reg)
    body = rgg._conventions(layer)
    first = [l for l in body.splitlines() if l.strip()][0]
    assert first in text, "conventions not carried through verbatim"
    assert "single-sourced from" in text, "provenance comment missing"


def test_uncovered_surface_is_marked_as_a_gap():
    reg = _reg()
    app = next((r for r in reg["source_repositories"]
                if r.get("contract") and rgg._harvest(r)), None)
    if app is None:
        pytest.skip("no harvestable contract available in this checkout")
    text = rgg.render(app["name"], reg)
    assert "[NO TEST]" in text or "Prioritise" in text


def test_render_rejects_an_unregistered_repo():
    with pytest.raises(KeyError):
        rgg.render("definitely-not-a-repo")


def test_render_is_deterministic_apart_from_the_timestamp():
    name = _an_app()
    a = [l for l in rgg.render(name).splitlines() if not l.startswith("> Generated")]
    b = [l for l in rgg.render(name).splitlines() if not l.startswith("> Generated")]
    assert a == b


# -------------------------------------------------------------------- precedence

def test_generated_never_outranks_a_real_repo_owned_file(gen_dir, tmp_path, monkeypatch):
    """The core guarantee: commit your own AGENTS.md and it wins."""
    import importlib, guidance_sync
    name = _clean_repo()
    rgg.ensure(name, force=True)
    assert rgg.generated_path(name).exists()

    sync = tmp_path / "synced"
    (sync / name).mkdir(parents=True)
    (sync / name / "AGENTS.md").write_text("# Real guidance\nuse the sandbox\n",
                                           encoding="utf-8")
    monkeypatch.setenv("AIQE_SYNC_DIR", str(sync))
    importlib.reload(guidance_sync)
    importlib.reload(repo_admin)
    try:
        picked = [f["path"] for f in repo_admin.repo_local_files(name)]
        # Compare against the real paths, not substrings: pytest's tmp dir is named
        # after this test, so it contains the word "generated" itself.
        gen = rgg.generated_path(name).as_posix()
        assert any((sync / name / "AGENTS.md").as_posix() in p for p in picked), \
            f"real guidance lost: {picked}"
        assert not any(gen in p for p in picked), \
            f"generated file shadowed the real one: {picked}"
    finally:
        monkeypatch.delenv("AIQE_SYNC_DIR", raising=False)
        importlib.reload(guidance_sync)
        importlib.reload(repo_admin)


def test_ensure_stands_down_when_the_repo_has_its_own(gen_dir, monkeypatch):
    name = _an_app()
    monkeypatch.setattr(rgg, "has_real_guidance", lambda n: True)
    r = rgg.ensure(name)
    assert r["status"] == "skipped_has_own"
    assert not rgg.generated_path(name).exists(), "wrote despite real guidance"


def test_has_real_guidance_ignores_our_own_output(gen_dir):
    """Otherwise the first generated file would block all later regeneration."""
    name = _clean_repo()
    rgg.ensure(name, force=True)
    assert rgg.MARKER in rgg.generated_path(name).read_text(encoding="utf-8")
    assert rgg.has_real_guidance(name) is False


def test_ensure_is_idempotent_and_force_overwrites(gen_dir):
    name = _clean_repo()
    assert rgg.ensure(name)["status"] == "written"
    assert rgg.ensure(name)["status"] == "skipped_exists"
    assert rgg.ensure(name, force=True)["status"] == "written"


def test_unregistered_repo_is_reported_not_raised(gen_dir):
    """Called opportunistically after an add — must never take the caller down."""
    assert rgg.ensure("no-such-repo")["status"] == "unregistered"


# ------------------------------------------------------ it reaches the phases

def test_generated_guidance_flows_into_guidance_for(gen_dir):
    name = _clean_repo()
    rgg.ensure(name, force=True)
    sources = [src for src, _ in repo_admin.guidance_for(name)]
    assert any("generated" in s for s in sources), \
        f"generated guidance never reaches the phases: {sources}"


def test_adding_a_repo_generates_guidance_for_both_kinds():
    """The user-facing behaviour: add a repo in the UI, get an AGENTS.md."""
    src = (ROOT / "engine/lib/repo_admin.py").read_text(encoding="utf-8")
    assert "_ensure_guidance" in src
    assert src.count('"guidance": _ensure_guidance(name, creating)') == 2, \
        "both upsert_app and upsert_test must generate guidance on add"
    assert "if not creating:" in src, "editing an existing repo must not regenerate"


def test_registry_mutators_still_hold_the_lock():
    """Guard against an insertion stealing @_locked from a mutator."""
    src = (ROOT / "engine/lib/repo_admin.py").read_text(encoding="utf-8")
    for fn in ("upsert_app", "upsert_test", "set_scope", "remove_app", "remove_test"):
        assert f"@_locked\ndef {fn}(" in src, f"{fn} lost its registry lock"
    assert "@_locked\ndef _ensure_guidance" not in src, \
        "_ensure_guidance must not take the registry lock"


# -------------------------------------------------------------------- surfaces

def test_cli_and_make_target_exist():
    r = subprocess.run([sys.executable, str(ROOT / "bin/repos.py"), "gen-guidance",
                        _a_test_repo(), "--show"], cwd=ROOT, capture_output=True,
                       text=True, encoding="utf-8", errors="replace",
                       stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stderr
    assert "AGENTS.md" in r.stdout
    assert "repo-agents:" in (ROOT / "Makefile").read_text(encoding="utf-8")


def test_dashboard_exposes_generation():
    server = (ROOT / "bin/dashboard_server.py").read_text(encoding="utf-8")
    assert "/api/repos/guidance" in server
    ui = (ROOT / "bin/dashboard.py").read_text(encoding="utf-8")
    assert "gen-guidance-all" in ui and "gen-guidance-one" in ui


def test_nothing_is_pushed_and_nothing_shells_out():
    """We write locally; the gate stays the only component that commits or pushes.

    Inspected via the AST — the module docstring legitimately *discusses* commits and
    pushes while explaining why it performs neither, so a substring scan would be a
    test of prose rather than of behaviour.
    """
    tree = ast.parse((ROOT / "engine/lib/repo_guidance_gen.py").read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            imported.add(node.module.split(".")[0])
    for forbidden in ("subprocess", "urllib", "requests", "socket", "http"):
        assert forbidden not in imported, \
            f"generator must not reach the network or shell out (imports {forbidden})"

    # and the only file it opens for writing is under knowledge/generated/
    writes = [n for n in ast.walk(tree)
              if isinstance(n, ast.Call) and getattr(n.func, "id", "") == "open"
              and any(isinstance(a, ast.Constant) and "w" in str(a.value)
                      for a in n.args[1:])]
    assert len(writes) <= 1, "unexpected extra write site in the generator"
