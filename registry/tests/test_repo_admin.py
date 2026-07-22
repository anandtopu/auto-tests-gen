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
    assert "demo/orders-api/CLAUDE.md" in agents
    assert "invalid codes return **422**" in agents        # the file's content, merged


def test_summary_labels_kinds_and_scope():
    s = repo_admin.summary()
    kinds = {a["name"]: a["kind"] for a in s["app_repos"]}
    assert kinds["web-storefront-ui"] == "ui" and kinds["orders-api"] == "service"
    assert all("scope" in t and "covers" in t for t in s["test_repos"])
    orders = next(a for a in s["app_repos"] if a["name"] == "orders-api")
    assert "demo/orders-api/CLAUDE.md" in orders["local_files"]


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
