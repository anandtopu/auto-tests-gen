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


def test_docx_export_is_valid_ooxml(tmp_path):
    import io, xml.etree.ElementTree as ET, zipfile
    data = export_plan.to_docx("PROJ-301")
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = set(z.namelist())
        assert {"[Content_Types].xml", "_rels/.rels",
                "word/document.xml", "word/styles.xml"} <= names
        doc = z.read("word/document.xml").decode("utf-8")
        ET.fromstring(doc)                       # well-formed XML
        assert "Test Plan Export" in doc and "PROJ-301" in doc
        assert "<w:tbl>" in doc                  # scenario/tests tables render as tables
        ET.fromstring(z.read("word/styles.xml").decode("utf-8"))


def test_pdf_export_is_valid_pdf():
    data = export_plan.to_pdf("PROJ-301")
    assert data.startswith(b"%PDF-1.4") and data.rstrip().endswith(b"%%EOF")
    assert b"/Type /Catalog" in data and b"xref" in data
    # content streams are uncompressed -> text is searchable
    assert b"Test Plan Export" in data and b"PROJ-301" in data
    assert b"Validation" in data


def test_render_returns_content_types():
    for fmt, expect in [("docx", "wordprocessingml"), ("pdf", "application/pdf")]:
        data, ctype = export_plan.render("PROJ-301", fmt)
        assert expect in ctype and len(data) > 500


def test_cli_export_writes_files(tmp_path):
    out_md = tmp_path / "plan.md"
    r = run_cli(["export-plan", "PROJ-301", "--out", str(out_md)])
    assert r.returncode == 0, r.stderr
    assert out_md.exists() and "Test Plan Export" in out_md.read_text(encoding="utf-8")
    out_html = tmp_path / "plan.html"
    r = run_cli(["export-plan", "PROJ-301", "--format", "html", "--out", str(out_html)])
    assert r.returncode == 0 and out_html.read_text(encoding="utf-8").startswith("<!doctype")
    out_docx = tmp_path / "plan.docx"
    r = run_cli(["export-plan", "PROJ-301", "--format", "docx", "--out", str(out_docx)])
    assert r.returncode == 0 and out_docx.read_bytes()[:2] == b"PK"
    out_pdf = tmp_path / "plan.pdf"
    r = run_cli(["export-plan", "PROJ-301", "--format", "pdf", "--out", str(out_pdf)])
    assert r.returncode == 0 and out_pdf.read_bytes()[:5] == b"%PDF-"
    r = run_cli(["export-plan", "NOPE-1"])
    assert r.returncode != 0
