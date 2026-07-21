"""Regression tests for the QA CLI (bin/qa.py) and catalog portability."""
import glob, importlib.util, json, pathlib, subprocess, sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]

spec = importlib.util.spec_from_file_location("qa", ROOT / "bin/qa.py")
qa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qa)


def test_catalog_paths_are_posix():
    """Windows backslashes must never leak into catalog test ids/files (gate greps them)."""
    for f in glob.glob(str(ROOT / "catalog/*.jsonl")):
        if pathlib.Path(f).name == "catalog.sample.jsonl":
            continue
        for line in open(f, encoding="utf-8"):
            if not line.strip():
                continue
            e = json.loads(line)
            assert "\\" not in e["file"], f"backslash path in {f}: {e['file']}"
            assert "\\" not in e["test_id"], f"backslash test_id in {f}: {e['test_id']}"


def test_api_tests_classified_as_api_layer():
    """fetch()-style API evidence must yield layer=api (was misclassified as ui)."""
    entries = [json.loads(l) for l in open(ROOT / "catalog/e2e-api-tests-1.jsonl", encoding="utf-8")]
    with_endpoints = [e for e in entries if e["evidence"]["endpoints"]]
    assert with_endpoints, "expected API evidence in e2e-api-tests-1 catalog"
    assert all(e["layer"] == "api" for e in with_endpoints)


def test_set_mapping_confirm_and_orphan():
    entry = {"mapping": {"app_repos": ["x"], "services": ["x"], "status": "needs_review",
                         "confidence": 0.6, "method": ["contract_match"]}}
    qa._set_mapping(entry, "orders-api")
    assert entry["mapping"]["status"] == "confirmed"
    assert entry["mapping"]["app_repos"] == ["orders-api"]
    assert "human_review" in entry["mapping"]["method"]

    qa._set_mapping(entry, "ORPHAN")
    assert entry["mapping"]["status"] == "orphan"
    assert entry["mapping"]["app_repos"] == []


def test_set_mapping_rejects_unregistered_repo():
    entry = {"mapping": {"app_repos": [], "services": [], "status": "needs_review",
                         "confidence": 0.6, "method": []}}
    with pytest.raises(SystemExit):
        qa._set_mapping(entry, "no-such-repo")


def test_qa_status_and_coverage_run_clean():
    for sub in ("status", "coverage", "review"):
        r = subprocess.run([sys.executable, str(ROOT / "bin/qa.py"), sub],
                           capture_output=True, text=True, cwd=ROOT,
                           stdin=subprocess.DEVNULL)
        assert r.returncode == 0, f"qa.py {sub} failed: {r.stderr}"


def test_qa_artifacts_view():
    """artifacts <KEY> shows plan/scenarios/tests for a recorded run (JIRA + PR keys)."""
    r = subprocess.run([sys.executable, str(ROOT / "bin/qa.py"), "artifacts", "PROJ-301"],
                       capture_output=True, text=True, cwd=ROOT, stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stderr
    assert "testplans/PROJ-301.md" in r.stdout
    assert "Generated tests:" in r.stdout
    # PR keys resolve with or without the PR- prefix
    r2 = subprocess.run([sys.executable, str(ROOT / "bin/qa.py"), "artifacts", "orders-api-201"],
                        capture_output=True, text=True, cwd=ROOT, stdin=subprocess.DEVNULL)
    assert r2.returncode == 0 and "PR-orders-api-201" in r2.stdout
    # unknown key fails with the known-key hint, not a traceback
    r3 = subprocess.run([sys.executable, str(ROOT / "bin/qa.py"), "artifacts", "NOPE-1"],
                        capture_output=True, text=True, cwd=ROOT, stdin=subprocess.DEVNULL)
    assert r3.returncode != 0 and "Known keys" in (r3.stdout + r3.stderr)
