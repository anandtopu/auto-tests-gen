"""Regression tests for repository administration (engine/lib/repo_admin.py):
add/edit/map app + E2E test repos, declared scope -> generated covers, and
per-repo agent guidance merged into AGENTS.md."""
import pathlib, subprocess, sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
import repo_admin
from registry import load_registry

REG = ROOT / "registry/repo-registry.yaml"
AGENTS = ROOT / "AGENTS.md"
NOTES = ROOT / "knowledge/repos"


@pytest.fixture
def estate_guard():
    """Snapshot registry + AGENTS.md; restore after, removing zz-* test notes."""
    reg_before, agents_before = REG.read_text(encoding="utf-8"), \
        AGENTS.read_text(encoding="utf-8")
    try:
        yield
    finally:
        REG.write_text(reg_before, encoding="utf-8")
        AGENTS.write_text(agents_before, encoding="utf-8")
        for p in NOTES.glob("zz-*.md"):
            p.unlink()
        (NOTES / "orders-api.md").unlink(missing_ok=True)


def _test_repo(name):
    return next(t for t in load_registry()["test_repositories"] if t["name"] == name)


def test_add_edit_link_and_remove_app_repo(estate_guard):
    r = repo_admin.upsert_app("zz-payments-ui", kind="ui", url="workspace/zz-payments-ui",
                              domains="payments", route_table="src/routes.tsx")
    assert r["created"]
    entry = next(x for x in load_registry()["source_repositories"]
                 if x["name"] == "zz-payments-ui")
    assert entry["type"] == "frontend" and entry["scm"] == "bitbucket"
    # edit only one field; link to a service via consumes
    repo_admin.upsert_app("zz-payments-ui", domains="payments,checkout",
                          consumes_services="orders-api")
    reg = load_registry()
    entry = next(x for x in reg["source_repositories"] if x["name"] == "zz-payments-ui")
    assert entry["domains"] == ["payments", "checkout"]
    assert entry["consumes_services"] == ["orders-api"]
    orders = next(x for x in reg["source_repositories"] if x["name"] == "orders-api")
    assert "zz-payments-ui" in orders["consumed_by"]      # reverse link maintained
    repo_admin.remove_app("zz-payments-ui")
    reg = load_registry()
    assert not any(x["name"] == "zz-payments-ui" for x in reg["source_repositories"])
    orders = next(x for x in reg["source_repositories"] if x["name"] == "orders-api")
    assert "zz-payments-ui" not in orders.get("consumed_by", [])


def test_scope_mapping_feeds_generated_covers(estate_guard):
    repo_admin.upsert_app("zz-billing-api", kind="service",
                          url="workspace/zz-billing-api", domains="billing")
    repo_admin.set_scope("e2e-api-tests-2", "zz-billing-api")
    t = _test_repo("e2e-api-tests-2")
    assert t["scope"] == ["zz-billing-api"]
    assert "zz-billing-api" in t["covers"]                # covers = evidence UNION scope
    # evidence-based coverage elsewhere is untouched
    assert "orders-api" in _test_repo("e2e-api-tests-1")["covers"]
    # scoped repo can't be removed silently; clearing the scope frees it
    with pytest.raises(SystemExit, match="covered by"):
        repo_admin.remove_app("zz-billing-api")
    repo_admin.set_scope("e2e-api-tests-2", "")
    assert "zz-billing-api" not in _test_repo("e2e-api-tests-2")["covers"]
    repo_admin.remove_app("zz-billing-api")


def test_upsert_and_remove_test_repo(estate_guard):
    r = repo_admin.upsert_test("zz-e2e-mobile", layer="ui", framework="playwright",
                               url="workspace/zz-e2e-mobile", specs="specs/",
                               scope="web-storefront-ui,admin-portal-ui")
    assert r["created"]
    t = _test_repo("zz-e2e-mobile")
    assert t["layer"] == "ui" and t["layout"]["specs"] == "specs/"
    assert t["covers"] == ["admin-portal-ui", "web-storefront-ui"]  # scope-driven
    repo_admin.remove_test("zz-e2e-mobile")                # no catalog entries -> ok
    assert not any(x["name"] == "zz-e2e-mobile"
                   for x in load_registry()["test_repositories"])
    with pytest.raises(SystemExit, match="cataloged"):
        repo_admin.remove_test("e2e-api-tests-1")          # has evidence -> guarded


def test_validation_rejects_bad_input(estate_guard):
    with pytest.raises(SystemExit):
        repo_admin.upsert_app("Bad Name!", kind="ui", url="x/y")
    with pytest.raises(SystemExit, match="kind"):
        repo_admin.upsert_app("zz-x", kind="mobile", url="x/y")
    with pytest.raises(SystemExit, match="scm"):
        repo_admin.upsert_app("zz-x", kind="ui", url="x/y", scm="gitlab")
    with pytest.raises(SystemExit, match="unknown service"):
        repo_admin.upsert_app("zz-x", kind="ui", url="x/y",
                              consumes_services="no-such-api")
    with pytest.raises(SystemExit, match="unknown app"):
        repo_admin.set_scope("e2e-api-tests-2", "no-such-repo")
    with pytest.raises(SystemExit, match="not a registered repo"):
        repo_admin.get_notes("no-such-repo")


def test_notes_roundtrip_merges_into_agents_md(estate_guard):
    repo_admin.set_notes("orders-api", "ZZ-GUIDE: discounts always return 201.")
    assert (NOTES / "orders-api.md").exists()
    agents = AGENTS.read_text(encoding="utf-8")
    assert "## Repository guidance" in agents
    assert "ZZ-GUIDE: discounts always return 201." in agents
    n = repo_admin.get_notes("orders-api")
    assert "ZZ-GUIDE" in n["team"]
    assert any("CLAUDE.md" in f["path"] for f in n["local_files"])
    repo_admin.set_notes("orders-api", "")                 # clear
    assert not (NOTES / "orders-api.md").exists()


def test_repo_local_claude_md_reaches_generation_context():
    subprocess.run([sys.executable, str(ROOT / "bin/gen_agents_md.py")], cwd=ROOT,
                   check=True, capture_output=True, stdin=subprocess.DEVNULL)
    agents = AGENTS.read_text(encoding="utf-8")
    # Any legitimate source may supply it — workspace clone during a run, the
    # knowledge/synced cache after an SCM sync, or the demo fixture.
    assert any(src in agents for src in ("workspace/src/orders-api/CLAUDE.md",
                                         "knowledge/synced/orders-api/CLAUDE.md",
                                         "demo/orders-api/CLAUDE.md"))
    assert "invalid codes return **422**" in agents        # the file's content, merged


def test_summary_labels_kinds_and_scope():
    s = repo_admin.summary()
    kinds = {a["name"]: a["kind"] for a in s["app_repos"]}
    assert kinds["web-storefront-ui"] == "ui" and kinds["orders-api"] == "service"
    assert all("scope" in t and "covers" in t for t in s["test_repos"])
    orders = next(a for a in s["app_repos"] if a["name"] == "orders-api")
    assert any(p.endswith("orders-api/CLAUDE.md") for p in orders["local_files"])


def test_cli_add_scope_and_notes(estate_guard):
    def cli(*args):
        return subprocess.run([sys.executable, str(ROOT / "bin/repos.py"), *args],
                              capture_output=True, text=True, cwd=ROOT,
                              stdin=subprocess.DEVNULL)
    r = cli("add-app", "zz-cli-api", "--kind", "service", "--url", "w/zz-cli-api")
    assert r.returncode == 0 and "added app repo" in r.stdout
    r = cli("scope", "e2e-api-tests-2", "zz-cli-api")
    assert r.returncode == 0 and "covers regenerated" in r.stdout
    r = cli("notes", "zz-cli-api", "--set", "ZZ-CLI-NOTE: prefix ids with cli-")
    assert r.returncode == 0 and "merged into AGENTS.md" in r.stdout
    r = cli("notes", "zz-cli-api")
    assert "ZZ-CLI-NOTE" in r.stdout
    assert "ZZ-CLI-NOTE" in AGENTS.read_text(encoding="utf-8")


def test_dashboard_renders_repos_view():
    r = subprocess.run([sys.executable, str(ROOT / "bin/dashboard.py")],
                       capture_output=True, text=True, cwd=ROOT,
                       stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stderr
    page = (ROOT / "reports/dashboard.html").read_text(encoding="utf-8")
    assert 'data-view="repos"' in page
    assert 'id="app-save"' in page and 'id="test-save"' in page
    assert "scope-save" in page and 'id="notes-repo"' in page


# --------------------------------------------------- bin/repos.py remove dispatch

def _repos_py(*args, expect=0):
    import subprocess, sys as _s
    r = subprocess.run([_s.executable, str(ROOT / "bin/repos.py"), *args], cwd=ROOT,
                       capture_output=True, text=True, encoding="utf-8",
                       errors="replace", stdin=subprocess.DEVNULL)
    assert r.returncode == expect, f"exit {r.returncode}: {r.stdout}{r.stderr}"
    return r.stdout + r.stderr


def test_cli_remove_handles_a_test_repo(estate_guard):
    """It used to only look at source_repositories, so a test repo was 'not registered'."""
    repo_admin.upsert_test("zz-rm-test", layer="api", framework="playwright-api",
                           url="org/zz-rm-test")
    assert repo_admin.summary()["test_repos"][-1]["name"] == "zz-rm-test"
    out = _repos_py("remove", "zz-rm-test")
    assert "removed test repo zz-rm-test" in out, out
    assert not any(t["name"] == "zz-rm-test"
                   for t in repo_admin.summary()["test_repos"])


def test_cli_remove_still_handles_an_app_repo(estate_guard):
    repo_admin.upsert_app("zz-rm-app", kind="service", url="org/zz-rm-app")
    out = _repos_py("remove", "zz-rm-app")
    assert "removed app repo zz-rm-app" in out, out
    assert not any(r["name"] == "zz-rm-app"
                   for r in repo_admin.summary()["app_repos"])


def test_cli_remove_reports_an_unknown_name(estate_guard):
    out = _repos_py("remove", "zz-not-a-repo", expect=1)
    assert "not registered" in out


def test_removing_an_app_repo_clears_dangling_scope(estate_guard):
    """The old hand-rolled CLI path left the name in every test repo's scope."""
    repo_admin.upsert_app("zz-scoped-app", kind="service", url="org/zz-scoped-app")
    repo_admin.upsert_test("zz-scope-holder", layer="api", framework="playwright-api",
                           url="org/zz-scope-holder", scope="zz-scoped-app")
    holder = next(t for t in repo_admin.summary()["test_repos"]
                  if t["name"] == "zz-scope-holder")
    assert "zz-scoped-app" in holder["scope"]

    repo_admin.remove_app("zz-scoped-app", force=True)
    holder = next(t for t in repo_admin.summary()["test_repos"]
                  if t["name"] == "zz-scope-holder")
    assert "zz-scoped-app" not in holder["scope"], "dangling scope reference left behind"


def test_removal_drops_generated_guidance(estate_guard, tmp_path, monkeypatch):
    """Otherwise re-adding the name reuses guidance describing its OLD config."""
    import repo_guidance_gen as rgg
    monkeypatch.setattr(rgg, "GEN_DIR", tmp_path / "generated")
    repo_admin.upsert_app("zz-guided", kind="service", url="org/zz-guided")
    rgg.ensure("zz-guided", force=True)
    assert rgg.generated_path("zz-guided").exists()

    repo_admin.remove_app("zz-guided", force=True)
    assert not rgg.generated_path("zz-guided").exists(), \
        "stale generated guidance survived removal"


# ---------------------------------------- runtime container (no pytest installed)

def test_mutation_does_not_cry_wolf_when_pytest_is_absent(estate_guard, monkeypatch,
                                                          capsys):
    """The runtime image ships no pytest. A repo mutation there must skip the golden
    re-run with a neutral note, not claim the change 'broke routing goldens'."""
    import importlib.util as _ilu
    real = _ilu.find_spec

    def fake(name, *a, **k):
        return None if name == "pytest" else real(name, *a, **k)

    monkeypatch.setattr("importlib.util.find_spec", fake)
    monkeypatch.delenv("PYTEST_CURRENT_TEST", raising=False)   # pretend not under pytest
    assert repo_admin._pytest_available() is False
    repo_admin.upsert_app("zz-nopytest", kind="service", url="org/zz-nopytest")
    out = capsys.readouterr().out
    assert "broke routing goldens" not in out, "false alarm when pytest is merely absent"
    # the mutation itself still succeeded
    assert any(r["name"] == "zz-nopytest"
               for r in repo_admin.summary()["app_repos"])
