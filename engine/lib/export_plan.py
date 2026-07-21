#!/usr/bin/env python3
"""Export the generated test plan for a JIRA ticket as shareable Markdown,
standalone HTML, a Word document (.docx), or a PDF — the plan file
(testplans/<KEY>.md) enriched with everything a stakeholder needs: release/review
status, scenarios, canonical test data, the generated tests with validation
results, and where the commits landed.

Stdlib-only by design (this repo's toolchain is python3 + pyyaml): the .docx is
assembled as the OOXML zip it really is, and the PDF via a minimal native writer.

Used by bin/qa.py export-plan and the dashboard server's download endpoint.
"""
import glob, html, io, json, pathlib, re, sys, time, zipfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
import review_state

FORMATS = ("md", "html", "docx", "pdf")
CONTENT_TYPES = {
    "md": "text/markdown; charset=utf-8",
    "html": "text/html; charset=utf-8",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pdf": "application/pdf",
}


def _latest_run(key):
    runs = []
    for f in glob.glob(str(ROOT / "reports/runs/*.json")):
        if pathlib.Path(f).name in ("reviews.json", "queue.json", "hooks-seen.json"):
            continue
        try:
            r = json.load(open(f, encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if r.get("trigger", {}).get("key") == key:
            runs.append(r)
    return max(runs, key=lambda r: r.get("ts", 0)) if runs else None


def available_plans():
    return sorted(p.stem for p in (ROOT / "testplans").glob("*.md"))


def build(key):
    """Collect everything for the export; raises SystemExit with a helpful message."""
    plan_file = ROOT / f"testplans/{key}.md"
    if not plan_file.exists():
        sys.exit(f"no test plan for '{key}' (plans exist for: "
                 f"{', '.join(available_plans()) or 'none'})")
    run = _latest_run(key) or {}
    contracts = {p["name"]: p["contract"] for p in run.get("phases", [])}
    rev = review_state.load().get(key, {})
    data_dir = ROOT / f"testdata/{key}"
    return {
        "key": key,
        "plan_md": plan_file.read_text(encoding="utf-8").strip(),
        "run": run,
        "scenarios": contracts.get("testplan", {}).get("scenarios", []),
        "tests": contracts.get("generate", {}).get("tests", []),
        "open_questions": (contracts.get("generate", {}).get("open_questions")
                           or contracts.get("testplan", {}).get("open_questions", [])),
        "validate": contracts.get("validate", {}),
        "gates": run.get("gates", []),
        "review": rev,
        "data_files": sorted(f"testdata/{key}/{p.relative_to(data_dir).as_posix()}"
                             for p in data_dir.rglob("*") if p.is_file())
                      if data_dir.exists() else [],
    }


def to_markdown(key):
    b = build(key)
    rev, v = b["review"], b["validate"]
    lines = [f"# Test Plan Export — {b['key']}", ""]
    meta = [f"- Exported: {time.strftime('%Y-%m-%d %H:%M')}"]
    if b["run"]:
        meta.append(f"- Pipeline run: `{b['run']['run_id']}` ({b['run'].get('overall', '?')})")
    if rev.get("release"):
        meta.append(f"- Target release: **{rev['release']}**")
    if rev.get("status"):
        meta.append(f"- Team review: **{rev['status']}**"
                    + (f" (by {rev['reviewer']})" if rev.get("reviewer") else ""))
    lines += meta + ["", "---", "", b["plan_md"], ""]
    if b["data_files"]:
        lines += ["## Canonical test data", ""] + [f"- `{f}`" for f in b["data_files"]] + [""]
    if b["tests"]:
        lines += ["## Generated tests", "",
                  "| file | test | action |", "|---|---|---|"]
        lines += [f"| `{t['file']}` | {t.get('name', '')} | {t.get('action', '')} |"
                  for t in b["tests"]] + [""]
    if v:
        lines += ["## Validation", "",
                  f"{v.get('passed', '?')} passed, {v.get('failed', '?')} failed, "
                  f"{v.get('repair_loops', '?')} repair loop(s)", ""]
    committed = [g for g in b["gates"] if g.get("commit")]
    if committed:
        lines += ["## Commits", ""] + [
            f"- `{g['test_repo']}` @ `{g['commit']}` (branch `test/{key}-ai-qe`)"
            for g in committed] + [""]
    if b["open_questions"]:
        lines += ["## Open questions", ""] + [f"- {q}" for q in b["open_questions"]] + [""]
    return "\n".join(lines)


# --- shared markdown block parser (feeds the html / docx / pdf writers) --------

def _blocks(md):
    """Yield (kind, payload): heading(level,text) | para(text) | li(text) |
    table(list of row-lists) | hr."""
    table = []
    for line in md.splitlines() + [""]:
        s = line.strip()
        if table and not s.startswith("|"):
            yield ("table", table)
            table = []
        if not s:
            continue
        if s.startswith("|"):
            cells = [c.strip() for c in s.strip("|").split("|")]
            if not all(re.fullmatch(r":?-+:?", c) for c in cells):
                table.append(cells)
        elif s.startswith("#"):
            n = len(s) - len(s.lstrip("#"))
            yield ("heading", (min(n, 4), s.lstrip("# ")))
        elif s.startswith("- "):
            yield ("li", s[2:])
        elif s in ("---", "***"):
            yield ("hr", None)
        else:
            yield ("para", s)


def _runs(text):
    """Split inline markdown into runs: (text, style) with style in '', 'bold', 'code'."""
    out = []
    for part in re.split(r"(\*\*[^*]+\*\*|`[^`]+`)", text):
        if not part:
            continue
        if part.startswith("**") and part.endswith("**"):
            out.append((part[2:-2], "bold"))
        elif part.startswith("`") and part.endswith("`"):
            out.append((part[1:-1], "code"))
        else:
            out.append((part, ""))
    return out


def _plain(text):
    return "".join(t for t, _ in _runs(text))


# --- HTML ----------------------------------------------------------------------

def _md_to_html(md):
    def inline(s):
        return "".join(
            f"<code>{html.escape(t)}</code>" if st == "code"
            else f"<strong>{html.escape(t)}</strong>" if st == "bold"
            else html.escape(t)
            for t, st in _runs(s))
    out, in_ul = [], False
    for kind, payload in _blocks(md):
        if in_ul and kind != "li":
            out.append("</ul>"); in_ul = False
        if kind == "heading":
            n, text = payload
            out.append(f"<h{n}>{inline(text)}</h{n}>")
        elif kind == "para":
            out.append(f"<p>{inline(payload)}</p>")
        elif kind == "li":
            if not in_ul:
                out.append("<ul>"); in_ul = True
            out.append(f"<li>{inline(payload)}</li>")
        elif kind == "hr":
            out.append("<hr>")
        elif kind == "table":
            out.append("<table>")
            for i, row in enumerate(payload):
                tag = "th" if i == 0 else "td"
                out.append("<tr>" + "".join(f"<{tag}>{inline(c)}</{tag}>" for c in row) + "</tr>")
            out.append("</table>")
    if in_ul:
        out.append("</ul>")
    return "\n".join(out)


def to_html(key):
    body = _md_to_html(to_markdown(key))
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Test Plan — {html.escape(key)}</title>
<style>
:root {{ --bg:#fff; --ink:#1a1a19; --ink2:#5f5e5b; --line:#e4e2de; --card:#f7f6f4 }}
@media (prefers-color-scheme: dark) {{
  :root {{ --bg:#1a1a19; --ink:#f0efec; --ink2:#a5a39e; --line:#3a3936; --card:#242422 }} }}
body {{ margin:0 auto; max-width:860px; padding:32px 24px; background:var(--bg);
       color:var(--ink); font:15px/1.6 ui-sans-serif,system-ui,sans-serif }}
h1 {{ font-size:22px }} h2 {{ font-size:17px; margin-top:28px }} h3 {{ font-size:15px }}
code {{ background:var(--card); padding:1px 5px; border-radius:4px; font-size:13px }}
table {{ border-collapse:collapse; width:100%; font-size:14px; margin:10px 0 }}
th,td {{ text-align:left; padding:6px 10px; border-bottom:1px solid var(--line) }}
th {{ color:var(--ink2); font-weight:600 }}
hr {{ border:none; border-top:1px solid var(--line); margin:20px 0 }}
ul {{ padding-left:22px }}
</style></head><body>
{body}
</body></html>
"""


# --- Confluence (storage-format body + publish via the Knowledge port) ----------

def to_confluence(key):
    """Confluence storage-format body (XHTML subset) — one-way mirror of the plan."""
    note = ("<p><em>Mirrored one-way from Git by AI-QE — source of truth: "
            f"<code>testplans/{html.escape(key)}.md</code>. Do not edit here.</em></p>")
    return note + "<hr/>" + _md_to_html(to_markdown(key)).replace("<hr>", "<hr/>")


def publish_to_confluence(key, space=None, title=None):
    """Render + publish through the Knowledge port (mock unless AIQE_MOCK=0)."""
    import os, subprocess
    import work_queue
    body = to_confluence(key)
    tmp = ROOT / "out/confluence-publish.html"
    tmp.parent.mkdir(exist_ok=True)
    tmp.write_text(body, encoding="utf-8", newline="\n")
    mock = os.environ.get("AIQE_MOCK", "1") == "1"
    adapter = ROOT / ("adapters/mock/knowledge.sh" if mock
                      else "adapters/knowledge/confluence.sh")
    space = space or os.environ.get("CONFLUENCE_SPACE", "QA")
    # ASCII default: non-ASCII titles mojibake through the Windows bash boundary
    title = title or f"Test Plan - {key}"
    r = subprocess.run([work_queue.bash_exe(), str(adapter), "publish_doc",
                        space, title, str(tmp)],
                       cwd=ROOT, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if r.returncode != 0:
        sys.exit(f"publish failed: {r.stdout}{r.stderr}".strip())
    return r.stdout.strip()


def attach_to_jira(key, fmt="pdf"):
    """Export the plan and attach it to the JIRA ticket via the Tracker port
    (mock unless AIQE_MOCK=0)."""
    import os, subprocess
    import work_queue
    path = export(key, fmt)                       # reports/exports/<KEY>-testplan.<fmt>
    mock = os.environ.get("AIQE_MOCK", "1") == "1"
    adapter = ROOT / ("adapters/mock/tracker.sh" if mock else "adapters/tracker/jira.sh")
    r = subprocess.run([work_queue.bash_exe(), str(adapter), "attach", key, str(path)],
                       cwd=ROOT, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    if r.returncode != 0:
        sys.exit(f"attach failed: {r.stdout}{r.stderr}".strip())
    return r.stdout.strip()


# --- DOCX (OOXML zip, stdlib only) ----------------------------------------------

_W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def _x(s):
    return html.escape(s, quote=True)


def _docx_runs(text):
    out = []
    for t, st in _runs(text):
        rpr = ""
        if st == "bold":
            rpr = "<w:rPr><w:b/></w:rPr>"
        elif st == "code":
            rpr = '<w:rPr><w:rFonts w:ascii="Consolas" w:hAnsi="Consolas"/><w:sz w:val="19"/></w:rPr>'
        out.append(f'<w:r>{rpr}<w:t xml:space="preserve">{_x(t)}</w:t></w:r>')
    return "".join(out)


def _docx_p(text, style=None, bullet=False):
    ppr = f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style else ""
    body = _docx_runs(("• " + text) if bullet else text)
    return f"<w:p>{ppr}{body}</w:p>"


def to_docx(key):
    parts = []
    for kind, payload in _blocks(to_markdown(key)):
        if kind == "heading":
            n, text = payload
            parts.append(_docx_p(text, style=f"Heading{min(n, 3)}"))
        elif kind == "para":
            parts.append(_docx_p(payload))
        elif kind == "li":
            parts.append(_docx_p(payload, bullet=True))
        elif kind == "hr":
            parts.append('<w:p><w:pPr><w:pBdr><w:bottom w:val="single" w:sz="6" '
                         'w:space="1" w:color="AAAAAA"/></w:pBdr></w:pPr></w:p>')
        elif kind == "table":
            rows = []
            for i, row in enumerate(payload):
                cells = "".join(
                    '<w:tc><w:tcPr><w:tcW w:w="0" w:type="auto"/></w:tcPr>'
                    + _docx_p(f"**{c}**" if i == 0 and c else c) + "</w:tc>"
                    for c in row)
                rows.append(f"<w:tr>{cells}</w:tr>")
            parts.append(
                '<w:tbl><w:tblPr><w:tblW w:w="0" w:type="auto"/><w:tblBorders>'
                + "".join(f'<w:{s} w:val="single" w:sz="4" w:color="CCCCCC"/>'
                          for s in ("top", "left", "bottom", "right", "insideH", "insideV"))
                + "</w:tblBorders></w:tblPr>" + "".join(rows) + "</w:tbl>")
    document = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                f'<w:document {_W}><w:body>{"".join(parts)}'
                f"<w:sectPr/></w:body></w:document>")
    styles = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:styles {_W}>'
              + '<w:style w:type="paragraph" w:styleId="Normal" w:default="1">'
                '<w:name w:val="Normal"/><w:rPr><w:sz w:val="21"/></w:rPr></w:style>'
              + "".join(
                  f'<w:style w:type="paragraph" w:styleId="Heading{n}">'
                  f'<w:name w:val="heading {n}"/><w:basedOn w:val="Normal"/>'
                  f'<w:pPr><w:spacing w:before="{60 * (4 - n)}" w:after="80"/></w:pPr>'
                  f'<w:rPr><w:b/><w:sz w:val="{34 - 4 * n}"/></w:rPr></w:style>'
                  for n in (1, 2, 3))
              + "</w:styles>")
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                   '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml"/>'
                   "</Types>")
        z.writestr("_rels/.rels",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
                   "</Relationships>")
        z.writestr("word/_rels/document.xml.rels",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
                   "</Relationships>")
        z.writestr("word/styles.xml", styles)
        z.writestr("word/document.xml", document)
    return buf.getvalue()


# --- PDF (minimal native writer, stdlib only) -----------------------------------

_PAGE_W, _PAGE_H, _MARGIN = 612, 792, 56          # US Letter, 1" margins-ish


def _pdf_escape(s):
    s = s.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
    return s.encode("cp1252", errors="replace").decode("cp1252")


def _wrap(text, size, width, char_w):
    max_chars = max(10, int(width / (size * char_w)))
    words, line, out = text.split(), "", []
    for w in words:
        cand = f"{line} {w}".strip()
        if len(cand) <= max_chars:
            line = cand
        else:
            if line:
                out.append(line)
            line = w[:max_chars]
    if line:
        out.append(line)
    return out or [""]


def to_pdf(key):
    # layout pass: (font, size, text) lines with per-line spacing
    lines = []                                     # (font, size, text, gap_before)
    for kind, payload in _blocks(to_markdown(key)):
        if kind == "heading":
            n, text = payload
            size = {1: 19, 2: 14, 3: 12, 4: 11}[n]
            for seg in _wrap(_plain(text), size, _PAGE_W - 2 * _MARGIN, 0.55):
                lines.append(("F2", size, seg, 14 if n <= 2 else 10))
        elif kind == "para":
            for seg in _wrap(_plain(payload), 10.5, _PAGE_W - 2 * _MARGIN, 0.5):
                lines.append(("F1", 10.5, seg, 4))
        elif kind == "li":
            for i, seg in enumerate(_wrap(_plain(payload), 10.5,
                                          _PAGE_W - 2 * _MARGIN - 14, 0.5)):
                lines.append(("F1", 10.5, ("• " if i == 0 else "   ") + seg, 2))
        elif kind == "hr":
            lines.append(("F1", 10.5, "_" * 78, 8))
        elif kind == "table":
            widths = [max(len(_plain(r[i])) if i < len(r) else 0 for r in payload)
                      for i in range(max(len(r) for r in payload))]
            for i, row in enumerate(payload):
                cells = [_plain(c).ljust(widths[j])[:widths[j]]
                         for j, c in enumerate(row)]
                text = "  ".join(cells)
                for seg in _wrap(text, 8.5, _PAGE_W - 2 * _MARGIN, 0.6):
                    lines.append(("F3", 8.5, seg, 5 if i == 0 else 2))

    # paginate into content streams
    pages, stream, y = [], [], _PAGE_H - _MARGIN
    for font, size, text, gap in lines:
        lh = size * 1.35
        if y - (gap + lh) < _MARGIN:
            pages.append("\n".join(stream))
            stream, y = [], _PAGE_H - _MARGIN
        y -= gap + lh
        stream.append(f"BT /{font} {size} Tf {_MARGIN} {y:.1f} Td"
                      f" ({_pdf_escape(text)}) Tj ET")
    pages.append("\n".join(stream))

    # assemble objects: 1 catalog, 2 pages, then per-page (page, contents), fonts last
    objs = {}
    n_pages = len(pages)
    font_ids = {"F1": 3 + 2 * n_pages, "F2": 4 + 2 * n_pages, "F3": 5 + 2 * n_pages}
    kids = " ".join(f"{3 + 2 * i} 0 R" for i in range(n_pages))
    objs[1] = "<< /Type /Catalog /Pages 2 0 R >>"
    objs[2] = f"<< /Type /Pages /Kids [{kids}] /Count {n_pages} >>"
    fonts_res = " ".join(f"/{k} {v} 0 R" for k, v in font_ids.items())
    for i, content in enumerate(pages):
        data = content.encode("cp1252", errors="replace")
        objs[3 + 2 * i] = (f"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 {_PAGE_W} {_PAGE_H}]"
                           f" /Resources << /Font << {fonts_res} >> >>"
                           f" /Contents {4 + 2 * i} 0 R >>")
        objs[4 + 2 * i] = (f"<< /Length {len(data)} >>\nstream\n", data, b"\nendstream")
    for name, base in (("F1", "Helvetica"), ("F2", "Helvetica-Bold"), ("F3", "Courier")):
        objs[font_ids[name]] = (f"<< /Type /Font /Subtype /Type1 /BaseFont /{base}"
                                f" /Encoding /WinAnsiEncoding >>")

    out, offsets = io.BytesIO(), {}
    out.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n")
    for num in sorted(objs):
        offsets[num] = out.tell()
        out.write(f"{num} 0 obj\n".encode())
        body = objs[num]
        if isinstance(body, tuple):
            out.write(body[0].encode())
            out.write(body[1])
            out.write(body[2])
        else:
            out.write(body.encode())
        out.write(b"\nendobj\n")
    xref_at = out.tell()
    count = max(objs) + 1
    out.write(f"xref\n0 {count}\n".encode())
    out.write(b"0000000000 65535 f \n")
    for num in range(1, count):
        out.write(f"{offsets[num]:010d} 00000 n \n".encode())
    out.write(f"trailer\n<< /Size {count} /Root 1 0 R >>\n"
              f"startxref\n{xref_at}\n%%EOF\n".encode())
    return out.getvalue()


# --- entry ----------------------------------------------------------------------

def render(key, fmt):
    """Return (bytes, content_type) for any supported format."""
    if fmt not in FORMATS:
        sys.exit(f"format must be one of: {', '.join(FORMATS)}")
    if fmt == "md":
        data = to_markdown(key).encode("utf-8")
    elif fmt == "html":
        data = to_html(key).encode("utf-8")
    elif fmt == "docx":
        data = to_docx(key)
    else:
        data = to_pdf(key)
    return data, CONTENT_TYPES[fmt]


def export(key, fmt="md", out=None):
    data, _ = render(key, fmt)
    path = pathlib.Path(out) if out else ROOT / f"reports/exports/{key}-testplan.{fmt}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


if __name__ == "__main__":
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    p = export(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else "md",
               sys.argv[3] if len(sys.argv) > 3 else None)
    print(f"exported: {p}")
