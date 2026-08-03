"""`covers:` — the routing table, generated and never hand-edited.

The constitution says coverage maps are generated from catalog evidence, so a
bug here is not something a human corrects downstream: it silently routes runs
to the wrong test repos, or fails to route them at all.

Run against an ISOLATED estate via AIQE_REGISTRY_FILE / AIQE_CATALOG_DIR, so
these never touch the real registry.
"""
import json
import pathlib
import subprocess
import sys

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
SCRIPT = ROOT / "catalog/bootstrap/regen_coverage.py"


def _estate(tmp_path, rows, scope=None, sources=("orders-api", "billing-api")):
    reg = {
        "source_repositories": [{"name": s} for s in sources],
        "test_repositories": [{"name": "e2e-api-tests-1",
                               **({"scope": scope} if scope else {})}],
    }
    rf = tmp_path / "repo-registry.yaml"
    rf.write_text(yaml.safe_dump(reg, sort_keys=False), encoding="utf-8")
    cat = tmp_path / "catalog"
    cat.mkdir(exist_ok=True)
    (cat / "generated.jsonl").write_text(
        "\n".join(json.dumps(r) for r in rows) + ("\n" if rows else ""),
        encoding="utf-8")
    return rf, cat


def _run(rf, cat):
    import os
    env = {**os.environ, "AIQE_REGISTRY_FILE": str(rf),
           "AIQE_CATALOG_DIR": str(cat)}
    r = subprocess.run([sys.executable, str(SCRIPT)], capture_output=True,
                       text=True, cwd=ROOT, env=env, stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stderr
    reg = yaml.safe_load(rf.read_text(encoding="utf-8"))
    return reg["test_repositories"][0].get("covers", [])


def _row(app, status):
    return {"test_id": f"t-{app}-{status}", "test_repo": "e2e-api-tests-1",
            "file": "suites/x.spec.js",
            "mapping": {"app_repos": [app], "confidence": 0.9, "status": status}}


def test_only_confirmed_and_auto_mappings_route(tmp_path):
    """The ADR: never route on `needs_review`. A mapping a human has not
    accepted must not silently start directing runs at a repo — that is the
    whole point of having a review tier."""
    rf, cat = _estate(tmp_path, [
        _row("orders-api", "auto"),
        _row("billing-api", "needs_review"),
    ])
    assert _run(rf, cat) == ["orders-api"]

    rf, cat = _estate(tmp_path, [_row("billing-api", "confirmed")])
    assert _run(rf, cat) == ["billing-api"]

    rf, cat = _estate(tmp_path, [_row("billing-api", "orphan")])
    assert _run(rf, cat) == []


def test_declared_scope_routes_before_any_evidence_exists(tmp_path):
    """covers = evidence UNION scope, so a newly-onboarded app repo reaches its
    test repo on day one — before a single test has been mapped to it."""
    rf, cat = _estate(tmp_path, [], scope=["billing-api"])
    assert _run(rf, cat) == ["billing-api"]

    rf, cat = _estate(tmp_path, [_row("orders-api", "auto")], scope=["billing-api"])
    assert _run(rf, cat) == ["billing-api", "orders-api"]


def test_scope_naming_an_unknown_repo_is_ignored(tmp_path):
    """`scope` is hand-managed, so it can name a repo that was renamed or
    removed. Routing at a source repo the registry does not know would send a
    run somewhere that cannot be cloned."""
    rf, cat = _estate(tmp_path, [], scope=["billing-api", "deleted-repo"])
    assert _run(rf, cat) == ["billing-api"]


def test_the_output_is_sorted_and_deduplicated(tmp_path):
    """Two tests mapping the same app repo must not double it, and the order
    must be stable — `covers:` is committed, so an unstable order is a diff on
    every regeneration."""
    rf, cat = _estate(tmp_path, [
        _row("orders-api", "auto"),
        {**_row("orders-api", "confirmed"), "test_id": "t2"},
        _row("billing-api", "auto"),
    ])
    assert _run(rf, cat) == ["billing-api", "orders-api"]


def test_a_blank_line_in_the_catalog_does_not_break_regeneration(tmp_path):
    """JSONL files acquire trailing newlines; the routing table must not be
    lost to one."""
    rf, cat = _estate(tmp_path, [_row("orders-api", "auto")])
    p = cat / "generated.jsonl"
    p.write_text(p.read_text(encoding="utf-8") + "\n\n", encoding="utf-8")
    assert _run(rf, cat) == ["orders-api"]


def test_the_sample_fixture_is_never_treated_as_evidence(tmp_path):
    """catalog.sample.jsonl ships as documentation. Routing on it would invent
    coverage for repos an estate may not even have."""
    rf, cat = _estate(tmp_path, [_row("orders-api", "auto")])
    (cat / "catalog.sample.jsonl").write_text(
        json.dumps(_row("billing-api", "auto")) + "\n", encoding="utf-8")
    assert _run(rf, cat) == ["orders-api"]
