"""The documents we hand people actually open.

`make report FORMAT=pdf`, `make export-plan`, `make attach-plan` and the email
path all write files with STDLIB-ONLY writers -- no reportlab, no python-docx.
Hand-rolled PDF and OOXML is a classic place for a file to be produced, exit 0
reported, and the artifact to be unopenable. Nothing asserted otherwise: the
existing tests check that a file appears and that its bytes start with %PDF.

These were written after verifying the current output by hand (both PDFs sound,
all xref offsets correct, the .eml a valid multipart message). The point of a
pin is that it stays true, so the check that matters is the one a byte-count or
a magic-number check misses: the xref TABLE. Its offsets are absolute file
positions, so any edit to the writer that changes an object's length silently
invalidates every later entry -- readers then fail, or "repair" the file and
show something subtly different from what was written.
"""
import pathlib
import re
import sys
import zipfile

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
import export_plan  # noqa: E402

MD = """# Test plan PROJ-1

Some prose with a long line that will need wrapping across the page because it
keeps going well past the right margin of a portrait A4 page.

## Scenario 1
- a bullet
- another bullet

## Scenario 2
1. numbered
2. also numbered
"""


def _xref_report(b):
    """(startxref_ok, entries, wrong_offsets) for a PDF's cross-reference table."""
    m = re.search(rb"startxref\s+(\d+)\s+%%EOF", b)
    if not m:
        return False, 0, ["no startxref/%%EOF trailer"]
    off = int(m.group(1))
    if b[off:off + 4] != b"xref":
        return False, 0, [f"startxref {off} does not point at 'xref'"]
    tail = b[off:]
    head = re.match(rb"xref\s+(\d+)\s+(\d+)\s+", tail)
    if not head:
        return False, 0, ["xref table header is unparseable"]
    start, count = int(head.group(1)), int(head.group(2))
    entries = re.findall(rb"(\d{10}) (\d{5}) ([nf])", tail[:20 + count * 20 + 40])
    wrong = []
    for i, (o, _gen, kind) in enumerate(entries):
        if kind != b"n":
            continue
        num = start + i
        if not re.match(rb"%d 0 obj" % num, b[int(o):int(o) + 20]):
            wrong.append(f"object {num} at offset {int(o)}")
    return True, len(entries), wrong


def test_the_pdf_is_structurally_complete():
    b = export_plan.md_to_pdf(MD)
    assert b.startswith(b"%PDF-"), "not a PDF at all"
    for token in (b"/Type /Catalog", b"/Type /Page", b"trailer", b"%%EOF"):
        assert token in b, f"missing {token.decode()}"


def test_every_pdf_xref_offset_points_at_its_object():
    """The check a magic-number test cannot make. Offsets are absolute file
    positions: change any object's length and every later entry is wrong,
    which readers report as a damaged file rather than as our bug."""
    ok, n, wrong = _xref_report(export_plan.md_to_pdf(MD))
    assert ok, wrong
    assert n > 0, "the xref table is empty"
    assert not wrong, f"{len(wrong)} xref offset(s) do not land on their object: {wrong[:3]}"


def test_a_longer_document_keeps_its_xref_correct():
    """Length-sensitivity is the whole risk, so exercise a document big enough
    to paginate rather than only the short happy path."""
    big = MD + "\n".join(f"- item {i} with enough text to wrap the line" for i in range(400))
    ok, n, wrong = _xref_report(export_plan.md_to_pdf(big))
    assert ok and not wrong, wrong
    assert n >= 4, f"only {n} xref entries for a multi-page document"


def test_the_docx_is_a_valid_zip_with_the_parts_word_requires(tmp_path):
    p = tmp_path / "d.docx"
    p.write_bytes(export_plan.md_to_docx(MD))
    with zipfile.ZipFile(p) as z:
        names = z.namelist()
        for req in ("[Content_Types].xml", "word/document.xml", "_rels/.rels"):
            assert req in names, f"a .docx without {req} will not open"
        assert z.testzip() is None, "a member fails its CRC"
        body = z.read("word/document.xml").decode("utf-8")
    assert body.lstrip().startswith("<?xml"), "document.xml is not XML"
    assert "PROJ-1" in body, "the content never made it into the document"


def test_the_email_is_a_parseable_mime_message():
    import email as _email
    import email_notify
    msg = email_notify._build("[AI QE] subject", "plain text body",
                              "<p>html body</p>", "ai-qe@platform.local",
                              ["qa@example.com"])
    m = _email.message_from_bytes(msg.as_bytes())
    assert m.get("To") == "qa@example.com"
    assert "subject" in (m.get("Subject") or "")
    assert m.is_multipart(), (
        "a report mail carries markdown AND html; a single-part message loses "
        "one of them in whichever client the reader uses")
    payload = (m.get_payload(0).get_payload(decode=True) if m.is_multipart()
               else m.get_payload(decode=True))
    assert payload, "the message has no body"
