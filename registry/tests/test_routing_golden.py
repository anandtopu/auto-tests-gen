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


def test_pipeline_honors_the_resolver_skip():
    """The skip verdict above must actually END the run: without this guard a
    docs-only PR ran the full LLM phase chain (real API spend) and then posted a
    success build status for work that never existed."""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[2] / "engine/pipeline.sh") \
        .read_text(encoding="utf-8")
    assert "RESOLVE_SKIP" in src
    i = src.index("RESOLVE_SKIP")
    # The window is 900, not 200, because the branch now says WHICH kind of skip
    # it was -- an empty change list is not a finding that nothing testable
    # changed (test_empty_change_list.py). The guarantee this pin exists for is
    # unchanged: after that message the run ENDS, before any phase. Distance was
    # only ever a proxy for it, and a proxy that breaks when the message grows
    # is measuring the wrong thing.
    assert "exit 0" in src[i:i + 900], "the skip branch must end the run"
    assert i < src.index("PHASE triage"), "the skip check must run before any phase"
    # And no LLM phase may sneak between the message and the exit.
    assert "PHASE " not in src[i:src.index("exit 0", i)], \
        "a phase runs after RESOLVE_SKIP is printed but before the run ends"

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
