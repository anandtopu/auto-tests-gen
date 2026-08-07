"""Regression tests for the team status report (engine/lib/team_report.py).

The build/filter tests run against a SEEDED estate (fixture below), not live
state — a fresh clone or a just-cleared demo estate must not fail them (they
used to assert e.g. "some run has an updated action" against whatever run
history happened to exist)."""
import json, pathlib, subprocess, sys, time

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
import review_state
import team_report
import work_queue


def run_cli(args):
    return subprocess.run([sys.executable, str(ROOT / "bin/qa.py"), *args],
                          capture_output=True, text=True, cwd=ROOT,
                          stdin=subprocess.DEVNULL)


def _record(key, ktype, ts, overall, tests=(), gates=()):
    return {"run_id": f"r{int(ts)}", "ts": ts,
            "trigger": {"type": ktype, "key": key}, "overall": overall,
            "phases": [{"name": "generate",
                        "contract": {"tests": list(tests)}},
                       {"name": "validate",
                        "contract": {"repair_loops": 1}}],
            "gates": list(gates)}


@pytest.fixture
def estate(tmp_path, monkeypatch):
    """A deterministic mini-estate: 2 committed (old jira + fresh PR with a
    created AND an updated action), 1 quarantined, 1 no_changes."""
    runs = tmp_path / "reports/runs"
    runs.mkdir(parents=True)
    (tmp_path / "catalog").mkdir()
    now = time.time()
    gate_ok = [{"test_repo": "e2e-api-tests-1", "status": "committed",
                "commit": "abc1234def"}]
    for rec in (
        _record("PR-orders-api-9", "pr", now - 60, "committed",
                tests=[{"file": "a.spec.js", "action": "created"},
                       {"file": "b.spec.js", "action": "updated"}],
                gates=gate_ok),
        _record("PROJ-77", "jira", now - 3 * 86400, "committed",
                tests=[{"file": "c.spec.js", "action": "created"}], gates=gate_ok),
        _record("PR-orders-api-8", "pr", now - 120, "quarantined",
                gates=[{"test_repo": "e2e-api-tests-1", "status": "quarantined",
                        "commit": ""}]),
        _record("PROJ-78", "jira", now - 240, "no_changes"),
    ):
        (runs / f"{rec['run_id']}.json").write_text(json.dumps(rec),
                                                    encoding="utf-8")
    reviews = {
        "PR-orders-api-9": {"status": "pending_review", "release": "2026.09",
                            "updated": now, "history": []},
        "PROJ-77": {"status": "approved", "release": "2026.08",
                    "updated": now, "history": []},
        "PR-orders-api-8": {"status": "pending_review", "release": "2026.09",
                            "updated": now, "history": []},
    }
    (runs / "reviews.json").write_text(json.dumps(reviews), encoding="utf-8")
    monkeypatch.setattr(team_report, "ROOT", tmp_path)
    monkeypatch.setattr(review_state, "FILE", runs / "reviews.json")
    monkeypatch.setattr(work_queue, "FILE", runs / "queue.json")
    return tmp_path


def test_build_totals_are_consistent(estate):
    d = team_report.build()
    t = d["totals"]
    assert t["runs"] == 4
    assert t["runs"] == t["committed"] + t["quarantined"] + t["no_changes"]
    assert t["committed"] == len(d["completed"]) == 2
    assert t["quarantined"] == len(d["quarantined"]) == 1
    assert t["tests_generated"] == t["tests_created"] + t["tests_updated"] == 3
    assert t["tests_updated"] == 1       # actions are past-tense ("updated")
    assert sum(d["per_day"].values()) == t["runs"]
    # committed rows carry gate commits and review status fields
    for row in d["completed"]:
        assert row["key"] and row["gates"]
        assert any(g["status"] == "committed" and g["commit"] for g in row["gates"])


def test_state_files_never_parsed_as_runs(estate):
    assert set(team_report.STATE_FILES) == {"reviews.json", "queue.json",
                                            "hooks-seen.json"}
    keys = [r["trigger"]["key"] for r in team_report._runs()]
    assert len(keys) == 4 and all(keys)          # reviews.json never parsed


def test_days_and_release_filters_narrow_the_report(estate):
    all_time = team_report.build()
    windowed = team_report.build(days=1)         # excludes the 3-day-old PROJ-77
    assert windowed["totals"]["runs"] == all_time["totals"]["runs"] - 1
    rel = team_report.build(release="2026.09")
    keys = {r["key"] for r in rel["completed"] + rel["quarantined"]}
    assert keys == {"PR-orders-api-9", "PR-orders-api-8"}
    for p in rel["pending_review"]:
        assert p["release"] == "2026.09"


def test_markdown_has_all_sections():
    md = team_report.to_markdown()
    for section in ("# QA Team Report", "## Summary", "## Completed work",
                    "## Awaiting team review", "## Work queue", "## By release",
                    "## Throughput", "## Estate health"):
        assert section in md, section
    assert "Pipeline runs" in md and "Tests generated" in md


def test_review_refusal_is_not_reported_as_no_changes(estate):
    runs = estate / "reports/runs"
    now = time.time()
    rec = _record("PR-orders-api-10", "pr", now, "review_refused")
    rec["review_delivery"] = {
        "outcome": "refused", "fixes": ["Add the missing boundary case."]}
    (runs / "review-refused.json").write_text(json.dumps(rec), encoding="utf-8")
    d = team_report.build()
    assert d["totals"]["runs"] == 5
    assert d["totals"]["review_refused"] == 1
    assert d["totals"]["no_changes"] == 1
    assert d["review_refused"][0]["key"] == "PR-orders-api-10"
    md = team_report.to_markdown()
    assert "Agent-review refusals" in md
    assert "Add the missing boundary case." in md


def test_render_all_formats():
    md, _ = team_report.render("md")
    assert md.decode("utf-8").startswith("# QA Team Report")
    html_doc, ctype = team_report.render("html", days=30)
    assert html_doc.decode("utf-8").startswith("<!doctype html>")
    assert "<title>QA Team Report</title>" in html_doc.decode("utf-8")
    assert ctype == "text/html; charset=utf-8"
    assert team_report.render("docx")[0][:2] == b"PK"          # OOXML zip
    assert team_report.render("pdf")[0][:5] == b"%PDF-"


def test_export_writes_dated_file(tmp_path):
    out = team_report.export("md", days=7, out=tmp_path / "report.md")
    assert out.exists() and "## Summary" in out.read_text(encoding="utf-8")


def test_cli_report_prints_markdown_and_writes_files():
    r = run_cli(["report", "--days", "30"])
    assert r.returncode == 0, r.stderr
    assert "# QA Team Report" in r.stdout and "last 30 day(s)" in r.stdout
    r = run_cli(["report", "--format", "html"])
    assert r.returncode == 0, r.stderr
    assert "report written:" in r.stdout and ".html" in r.stdout


def test_dashboard_renders_report_card():
    r = subprocess.run([sys.executable, str(ROOT / "bin/dashboard.py")],
                       capture_output=True, text=True, cwd=ROOT,
                       stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stderr
    page = (ROOT / "reports/dashboard.html").read_text(encoding="utf-8")
    assert "Team report" in page and 'class="btn btn-sm report-dl"' in page
    assert 'id="rep-days"' in page and "/api/report?format=" in page
