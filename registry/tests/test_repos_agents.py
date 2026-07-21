"""Regression tests for repo configuration (bin/repos.py) and estate knowledge
generation (bin/gen_agents_md.py)."""
import pathlib, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
REG = ROOT / "registry/repo-registry.yaml"


def run(args, **kw):
    return subprocess.run([sys.executable, *args], capture_output=True, text=True,
                          cwd=ROOT, stdin=subprocess.DEVNULL, **kw)


def test_agents_md_generation_covers_estate():
    r = run([str(ROOT / "bin/gen_agents_md.py")])
    assert r.returncode == 0, r.stderr
    text = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    assert "AUTO-GENERATED" in text
    # every registered repo appears
    for name in ("orders-api", "web-storefront-ui", "e2e-api-tests-1"):
        assert name in text
    # harvested facts from the demo estate are present
    assert "/v1/orders/{id}/discounts" in text
    assert "/checkout/cart" in text
    # knowledge sections agents rely on
    for section in ("## Existing test coverage", "## Routing hints",
                    "## Conventions & non-negotiables"):
        assert section in text
    # coverage gaps are called out, not silently omitted
    assert "coverage gap" in text


def test_repos_list_and_show():
    assert run([str(ROOT / "bin/repos.py"), "list"]).returncode == 0
    r = run([str(ROOT / "bin/repos.py"), "show", "orders-api"])
    assert r.returncode == 0 and "covered_by" in r.stdout


def test_repos_rejects_unknown_repo_and_field():
    assert run([str(ROOT / "bin/repos.py"), "show", "no-such-repo"]).returncode != 0
    r = run([str(ROOT / "bin/repos.py"), "set", "orders-api", "bogus-field", "x"])
    assert r.returncode != 0 and "unknown field" in (r.stdout + r.stderr)


def test_repos_set_roundtrip_preserves_registry():
    before = REG.read_text(encoding="utf-8")
    try:
        r = run([str(ROOT / "bin/repos.py"), "set", "orders-api", "domains",
                 "checkout,orders,zz-test"])
        assert r.returncode == 0, r.stdout + r.stderr
        assert "zz-test" in REG.read_text(encoding="utf-8")
        assert "zz-test" in (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    finally:
        REG.write_text(before, encoding="utf-8")
        run([str(ROOT / "bin/gen_agents_md.py")])


def test_remove_refuses_while_covered():
    r = run([str(ROOT / "bin/repos.py"), "remove", "orders-api"])
    assert r.returncode != 0
    assert "still covered" in (r.stdout + r.stderr)
    # registry untouched
    assert "orders-api" in REG.read_text(encoding="utf-8")
