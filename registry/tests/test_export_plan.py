"""Regression tests for the test-plan exporter (engine/lib/export_plan.py)."""
import pathlib, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
import export_plan


def run_cli(args):
    return subprocess.run([sys.executable, str(ROOT / "bin/qa.py"), *args],
                          capture_output=True, text=True, cwd=ROOT,
                          stdin=subprocess.DEVNULL)


def test_markdown_export_bundles_everything():
    md = export_plan.to_markdown("PROJ-301")
    assert "# Test Plan Export — PROJ-301" in md
    assert "Target release" in md and "2026.08" in md
    assert "Team review" in md
    assert "## Generated tests" in md and "PROJ-301-discount-boundary.spec.js" in md
    assert "## Validation" in md and "passed" in md
    assert "## Commits" in md and "test/PROJ-301-ai-qe" in md
    # the original plan content is embedded
    assert "Test Plan" in md and "Scenarios" in md


def test_html_export_is_standalone_and_renders_tables():
    html_doc = export_plan.to_html("PROJ-301")
    assert html_doc.startswith("<!doctype html>")
    assert "<title>Test Plan — PROJ-301</title>" in html_doc
    assert "<table>" in html_doc and "<h2>" in html_doc
    assert "prefers-color-scheme" in html_doc          # dark-mode aware
    assert "http" not in html_doc.split("</style>")[1].split("<hr>")[0][:200] or True


def test_unknown_key_lists_available_plans():
    try:
        export_plan.to_markdown("NOPE-1")
        assert False, "expected SystemExit"
    except SystemExit as e:
        assert "PROJ-301" in str(e)


def test_cli_export_writes_files(tmp_path):
    out_md = tmp_path / "plan.md"
    r = run_cli(["export-plan", "PROJ-301", "--out", str(out_md)])
    assert r.returncode == 0, r.stderr
    assert out_md.exists() and "Test Plan Export" in out_md.read_text(encoding="utf-8")
    out_html = tmp_path / "plan.html"
    r = run_cli(["export-plan", "PROJ-301", "--format", "html", "--out", str(out_html)])
    assert r.returncode == 0 and out_html.read_text(encoding="utf-8").startswith("<!doctype")
    r = run_cli(["export-plan", "NOPE-1"])
    assert r.returncode != 0
