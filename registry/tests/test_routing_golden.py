"""Golden tests for the deterministic resolver (architecture §5.8.2, ADR-5)."""
import json, pathlib, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parents[2]

def run_resolve(*args):
    r = subprocess.run([sys.executable, str(ROOT / "engine/phases/resolve.py"), *args],
                       capture_output=True, text=True, cwd=ROOT, stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)

def test_frontend_pr_routes_to_ui_repo(tmp_path):
    f = tmp_path / "changed.txt"; f.write_text("src/checkout/Cart.tsx\n")
    out = run_resolve("pr", "web-storefront-ui", "--changed-files", str(f))
    assert out["source_repos"] == ["web-storefront-ui"]
    # covers[] is catalog-generated; empty pre-bootstrap → low confidence, needs human
    assert out["needs_clarification"] or "e2e-ui-tests-1" in out["test_repos"]

def test_docs_only_pr_skips(tmp_path):
    f = tmp_path / "changed.txt"; f.write_text("README.md\n")
    out = run_resolve("pr", "web-storefront-ui", "--changed-files", str(f))
    assert out.get("skip") is True and out["test_repos"] == []

def test_contract_change_fans_out(tmp_path):
    f = tmp_path / "changed.txt"; f.write_text("app/orders.py\nopenapi/orders.yaml\n")
    out = run_resolve("pr", "orders-api", "--changed-files", str(f))
    assert "web-storefront-ui" in out["source_repos"]          # consumer pulled in
    assert any(i["consumer"] == "web-storefront-ui" for i in out["cross_repo_impact"])

def test_jira_component_mapping():
    out = run_resolve("jira", "PROJ-123", "--components", "Checkout")
    assert set(out["source_repos"]) == {"web-storefront-ui", "orders-api"}

def test_jira_unmapped_asks_human():
    out = run_resolve("jira", "PROJ-999", "--components", "", "--labels", "")
    assert out["needs_clarification"] is True
