"""Per-repo Stash project resolution (engine/lib/stash_target.py + adapters/scm/stash.sh).

A real estate spreads repositories across several Stash projects — app repos under
one, E2E test repos under another — so the adapter must resolve each repo's project
and slug individually rather than assuming a single global STASH_PROJECT.
"""
import os, pathlib, subprocess, sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
import stash_target as st


# --------------------------------------------------------------- resolver

def _reg(entries):
    return {"source_repositories": entries, "test_repositories": []}


def test_project_comes_from_the_url_first_segment(monkeypatch):
    monkeypatch.setattr(st, "_entry", lambda n: {"name": n, "url": "ENG/orders-api"})
    assert st.resolve("orders-api", {}) == ("ENG", "orders-api")


def test_different_repos_resolve_to_different_projects(monkeypatch):
    table = {"orders-api": {"url": "ENG/orders-api"},
             "e2e-api": {"url": "QA/e2e-api"}}
    monkeypatch.setattr(st, "_entry", lambda n: {**table.get(n, {}), "name": n})
    assert st.resolve("orders-api", {})[0] == "ENG"
    assert st.resolve("e2e-api", {})[0] == "QA"


def test_explicit_stash_project_field_overrides_the_url(monkeypatch):
    monkeypatch.setattr(st, "_entry",
                        lambda n: {"name": n, "url": "ENG/thing", "stash_project": "OVERRIDE"})
    project, slug = st.resolve("thing", {})
    assert project == "OVERRIDE" and slug == "thing"


def test_slug_can_differ_from_the_registry_name(monkeypatch):
    monkeypatch.setattr(st, "_entry", lambda n: {"name": n, "url": "ENG/real-slug"})
    assert st.resolve("friendly-name", {})[1] == "real-slug"


def test_falls_back_to_the_default_project_env(monkeypatch):
    monkeypatch.setattr(st, "_entry", lambda n: {"name": n, "url": "just-a-slug"})
    assert st.resolve("just-a-slug", {"STASH_PROJECT": "DEF"}) == ("DEF", "just-a-slug")


def test_unknown_repo_falls_back_without_raising(monkeypatch):
    monkeypatch.setattr(st, "_entry", lambda n: None)
    assert st.resolve("ghost", {"STASH_PROJECT": "DEF"}) == ("DEF", "ghost")
    assert st.resolve("ghost", {}) == ("", "ghost")     # nothing available -> empty project


# --------------------------------------------------------------- CLI contract

def _cli(name, env):
    return subprocess.run([sys.executable, str(ROOT / "engine/lib/stash_target.py"), name],
                          cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                          errors="replace", stdin=subprocess.DEVNULL, env=env)


def test_cli_emits_tab_separated_project_and_slug():
    # orders-api is a real registry repo (github, url org/orders-api) — project derives
    # from its url first segment, "org".
    r = _cli("orders-api", {**os.environ, "STASH_PROJECT": ""})
    assert r.returncode == 0, r.stderr
    project, slug = r.stdout.rstrip("\n").split("\t")
    assert project and slug == "orders-api"


def test_cli_exit_3_when_no_project_can_be_determined():
    env = {k: v for k, v in os.environ.items() if k != "STASH_PROJECT"}
    r = _cli("definitely-not-a-registered-repo", env)
    assert r.returncode == 3
    assert "NO_STASH_PROJECT" in r.stderr


# ------------------------------------------------ adapter routing (end to end)

@pytest.fixture
def stubbed(tmp_path, monkeypatch):
    """A git/curl stub on PATH that echoes the URL the adapter builds, plus two
    temp repos registered under different Stash projects."""
    import repo_admin
    stub = tmp_path / "bin"
    stub.mkdir()
    for name in ("git", "curl"):
        (stub / name).write_text(
            '#!/usr/bin/env bash\nfor a in "$@"; do case "$a" in '
            'http*|/scm/*|*/projects/*) echo "URL: $a";; esac; done\n'
            'case "$1$*" in *raw*) echo "{}";; esac\nexit 0\n', encoding="utf-8")
        os.chmod(stub / name, 0o755)
    repo_admin.upsert_app("zz-st-eng", kind="service", scm="stash", url="ENG/zz-st-eng")
    repo_admin.upsert_test("zz-st-qa", layer="api", framework="playwright-api",
                           scm="stash", url="QA/zz-st-qa")
    try:
        yield stub
    finally:
        repo_admin.remove_app("zz-st-eng", force=True)
        repo_admin.remove_test("zz-st-qa", force=True)


def _run_adapter(stub, verb, *args):
    env = {**os.environ, "PATH": f"{stub}{os.pathsep}{os.environ['PATH']}",
           "AIQE_ROOT": str(ROOT), "STASH_URL": "https://stash.example.com",
           "STASH_TOKEN": "tok", "STASH_PROJECT": "DEFAULT"}
    # bash from the same place the platform uses (WSL stub avoidance)
    import work_queue
    return subprocess.run([work_queue.bash_exe(), str(ROOT / "adapters/scm/stash.sh"),
                           verb, *args], cwd=ROOT, capture_output=True, text=True,
                          encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL,
                          env=env)


def test_clone_routes_each_repo_to_its_own_project(stubbed):
    eng = _run_adapter(stubbed, "clone_ro", "zz-st-eng", "/tmp/x")
    qa = _run_adapter(stubbed, "clone_ro", "zz-st-qa", "/tmp/y")
    assert "/scm/ENG/zz-st-eng.git" in eng.stdout, eng.stdout + eng.stderr
    assert "/scm/QA/zz-st-qa.git" in qa.stdout, qa.stdout + qa.stderr


def test_rest_verbs_route_to_the_repos_project(stubbed):
    r = _run_adapter(stubbed, "fetch_file", "zz-st-qa", "AGENTS.md")
    assert "/projects/QA/repos/zz-st-qa/" in r.stdout, r.stdout + r.stderr


def test_repo_without_a_project_uses_the_default(stubbed):
    import repo_admin
    repo_admin.upsert_app("zz-st-bare", kind="service", scm="stash", url="zz-st-bare")
    try:
        r = _run_adapter(stubbed, "clone_ro", "zz-st-bare", "/tmp/z")
        assert "/scm/DEFAULT/zz-st-bare.git" in r.stdout, r.stdout + r.stderr
    finally:
        repo_admin.remove_app("zz-st-bare", force=True)


def test_adapter_still_rejects_unknown_verbs():
    """The exit-64 conformance contract must survive the per-repo changes."""
    import work_queue
    r = subprocess.run([work_queue.bash_exe(), str(ROOT / "adapters/scm/stash.sh"),
                        "definitely_unknown_verb"], cwd=ROOT, capture_output=True,
                       text=True, encoding="utf-8", errors="replace",
                       stdin=subprocess.DEVNULL)
    assert r.returncode == 64


# ------------------------------------------- per-repo project mapping (UI/CLI field)

def test_upsert_stores_an_explicit_project_that_beats_the_url(monkeypatch):
    import repo_admin
    try:
        repo_admin.upsert_app("zz-proj-map", kind="service", scm="stash",
                              url="URLPROJ/zz-proj-map", stash_project="OVERRIDE")
        entry = next(r for r in repo_admin.summary()["app_repos"]
                     if r["name"] == "zz-proj-map")
        assert entry["stash_project"] == "OVERRIDE"
        assert st.resolve("zz-proj-map", {}) == ("OVERRIDE", "zz-proj-map")
        # empty string REMOVES the override -> falls back to the url segment
        repo_admin.upsert_app("zz-proj-map", stash_project="")
        assert st.resolve("zz-proj-map", {}) == ("URLPROJ", "zz-proj-map")
    finally:
        repo_admin.remove_app("zz-proj-map", force=True)


def test_upsert_rejects_a_malformed_project_key():
    import repo_admin
    with pytest.raises(SystemExit):
        repo_admin.upsert_app("zz-bad-proj", kind="service", scm="stash",
                              url="X/zz-bad-proj", stash_project="not a key!")
    # and the half-validated repo must not have been left behind
    assert not any(r["name"] == "zz-bad-proj"
                   for r in repo_admin.summary()["app_repos"])


def test_project_field_is_exposed_on_every_surface():
    """CLI (add + set), server API, and both dashboard forms."""
    cli = (ROOT / "bin/repos.py").read_text(encoding="utf-8")
    assert "--stash-project" in cli and '"stash-project": "stash_project"' in cli
    srv = (ROOT / "bin/dashboard_server.py").read_text(encoding="utf-8")
    assert srv.count('stash_project=p.get("stash_project")') == 2, \
        "both /api/repos/app and /api/repos/test must pass the field"
    ui = (ROOT / "bin/dashboard.py").read_text(encoding="utf-8")
    assert "app-stashproj" in ui and "test-stashproj" in ui
