"""Regression tests for the team status report (engine/lib/team_report.py)."""
import pathlib, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
import team_report


def run_cli(args):
    return subprocess.run([sys.executable, str(ROOT / "bin/qa.py"), *args],
                          capture_output=True, text=True, cwd=ROOT,
                          stdin=subprocess.DEVNULL)


def test_build_totals_are_consistent():
    d = team_report.build()
    t = d["totals"]
    assert t["runs"] == t["committed"] + t["quarantined"] + t["no_changes"]
    assert t["committed"] == len(d["completed"])
    assert t["quarantined"] == len(d["quarantined"])
    assert t["tests_generated"] == t["tests_created"] + t["tests_updated"]
    assert t["tests_updated"] > 0        # actions are past-tense ("updated")
    assert sum(d["per_day"].values()) == t["runs"]
    # committed rows carry gate commits and review status fields
    for row in d["completed"]:
        assert row["key"] and row["gates"]
        assert any(g["status"] == "committed" and g["commit"] for g in row["gates"])


def test_state_files_never_parsed_as_runs():
    assert set(team_report.STATE_FILES) == {"reviews.json", "queue.json",
                                            "hooks-seen.json"}
    keys = [r["trigger"]["key"] for r in team_report._runs()]
    assert keys and all(keys)                     # every record is a real run


def test_days_and_release_filters_narrow_the_report():
    all_time = team_report.build()
    windowed = team_report.build(days=1)
    assert windowed["totals"]["runs"] <= all_time["totals"]["runs"]
    rel = team_report.build(release="2026.09")
    keys = {r["key"] for r in rel["completed"] + rel["quarantined"]}
    assert keys, "expected runs tracked against release 2026.09"
    assert all(k.startswith("PR-") for k in keys)
    for p in rel["pending_review"]:
        assert p["release"] == "2026.09"


def test_markdown_has_all_sections():
    md = team_report.to_markdown()
    for section in ("# QA Team Report", "## Summary", "## Completed work",
                    "## Awaiting team review", "## Work queue", "## By release",
                    "## Throughput", "## Estate health"):
        assert section in md, section
    assert "Pipeline runs" in md and "Tests generated" in md


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
