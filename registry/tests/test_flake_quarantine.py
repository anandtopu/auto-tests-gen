"""Flake quarantine (roadmap 1.2) — the human decision, tooled.

Pins: flaky listing reads CI health and marks already-quarantined rows; quarantine
is a catalog TAG through the sanctioned mutation path (never an edit to the test
repo — the printed exclusion is a proposal for the repo owner's own CI); lifting
removes tag and note; an unknown test id fails with guidance.
"""
import importlib.util
import json
import os
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))

spec = importlib.util.spec_from_file_location("qa_mod", ROOT / "bin/qa.py")
qa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(qa)


@pytest.fixture
def catalog(tmp_path, monkeypatch):
    cat = tmp_path / "catalog"
    cat.mkdir()
    entry = {"test_id": "t-repo::suites/a.spec.js::flaky one",
             "file": "suites/a.spec.js", "title": "flaky one",
             "mapping": {"app_repos": ["svc"], "status": "confirmed"}}
    (cat / "t-repo.jsonl").write_text(json.dumps(entry) + "\n", encoding="utf-8")
    monkeypatch.setattr(qa, "ROOT", tmp_path)
    return cat / "t-repo.jsonl"


def test_quarantine_tags_and_lift_untags(catalog, capsys):
    class A:
        test_id = "t-repo::suites/a.spec.js::flaky one"
        note = "fails 30% since build 812"
        lift = False

    qa.cmd_quarantine(A)
    out = capsys.readouterr().out
    assert "QUARANTINED" in out
    assert "exclude-from-required: suites/a.spec.js" in out, \
        "the exclusion is a PROPOSAL for the repo owner, not an action we take"
    e = json.loads(catalog.read_text(encoding="utf-8"))
    assert e["mapping"]["quarantined"] is True
    assert e["mapping"]["quarantine_note"] == "fails 30% since build 812"

    A.lift = True
    qa.cmd_quarantine(A)
    e = json.loads(catalog.read_text(encoding="utf-8"))
    # Lift POPS the tag — `"quarantined": false` residue in a tracked JSONL
    # made every quarantine cycle permanent git noise (UAT finding 4).
    assert "quarantined" not in e["mapping"]
    assert "quarantine_note" not in e["mapping"]


def test_unknown_test_id_fails_with_guidance(catalog):
    class A:
        test_id = "nope::missing.spec.js::x"
        note = ""
        lift = False

    with pytest.raises(SystemExit, match="no cataloged test"):
        qa.cmd_quarantine(A)


def test_flaky_listing_marks_quarantined_rows(catalog, tmp_path, monkeypatch, capsys):
    import test_health
    monkeypatch.setattr(test_health, "FILE", tmp_path / "health.json")
    (tmp_path / "health.json").write_text(json.dumps({
        "t-repo::suites/a.spec.js::flaky one":
            {"runs": 10, "failures": 3, "flaky": True, "last_status": "failed"},
        "t-repo::suites/b.spec.js::steady":
            {"runs": 10, "failures": 0, "flaky": False, "last_status": "passed"},
    }), encoding="utf-8")

    class A:
        test_id = "t-repo::suites/a.spec.js::flaky one"
        note = ""
        lift = False

    qa.cmd_quarantine(A)
    capsys.readouterr()
    qa.cmd_flaky(A)
    out = capsys.readouterr().out
    assert "flaky one" in out and "steady" not in out, "only flaky rows list"
    line = next(l for l in out.splitlines() if "flaky one" in l)
    assert " Q " in f" {line} ".replace("  ", " ") or " Q" in line, \
        "already-quarantined rows must be marked"


def test_empty_health_points_at_the_ingest_paths(tmp_path, monkeypatch, capsys):
    import test_health
    monkeypatch.setattr(test_health, "FILE", tmp_path / "none.json")
    qa.cmd_flaky(None)
    out = capsys.readouterr().out
    assert "/hooks/ci/results" in out and "ingest-results" in out


def test_dashboard_exposes_quarantine_decision_and_escaped_note(tmp_path):
    cat = tmp_path / "catalog"
    cat.mkdir()
    entry = {
        "test_id": "t-repo::suites/a.spec.js::flaky one",
        "test_repo": "e2e-api-tests-1",
        "file": "suites/a.spec.js",
        "title": "flaky one",
        "layer": "api",
        "tags": [],
        "evidence": {"endpoints": ["GET /v1/a"], "ui_routes": [], "fixtures": [],
                     "git_jira_keys": []},
        "mapping": {"app_repos": ["orders-api"], "services": ["orders-api"],
                    "status": "confirmed", "confidence": 1.0,
                    "method": ["human_review"], "quarantined": True,
                    "quarantine_note": "fails <sometimes> & needs review"},
    }
    (cat / "t-repo.jsonl").write_text(json.dumps(entry) + "\n", encoding="utf-8")
    health = cat / "health.json"
    health.write_text("{}", encoding="utf-8")

    r = subprocess.run(
        [sys.executable, str(ROOT / "bin/dashboard.py")],
        cwd=ROOT, capture_output=True, text=True, stdin=subprocess.DEVNULL,
        env={**os.environ, "AIQE_CATALOG_DIR": str(cat),
             "AIQE_HEALTH_FILE": str(health)},
        timeout=120,
    )

    assert r.returncode == 0, r.stderr
    html = (ROOT / "reports/dashboard.html").read_text(encoding="utf-8")
    assert "⚠ quarantined" in html
    assert "fails &lt;sometimes&gt; &amp; needs review" in html
    assert "fails <sometimes>" not in html
