"""Counts and versions stated in prose must match what is actually there.

docs/review-readonly-rootfs.md claimed `readOnlyRootFilesystem` was OFF for as
long as it had been ON — and CLAUDE.md itself records why that direction of
staleness is the dangerous one: it invites the next person to "fix" the code by
undoing the hardening. The same trap sits in every stated count. "22 Mermaid
diagrams" reads as a specification once it is wrong: a reader who finds 24 may
delete two, and a reader who finds 20 will not go looking for the missing ones.

These pin the small set of numbers CLAUDE.md and the docs assert about each
other. They are cheap, and each one has already been wrong at least once.
"""
import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _read(rel):
    return (ROOT / rel).read_text(encoding="utf-8", errors="replace")


def test_claude_md_diagram_count_matches_the_file():
    claimed = re.search(r"`docs/diagrams\.md` \((\d+) Mermaid diagrams", _read("CLAUDE.md"))
    assert claimed, "CLAUDE.md no longer states a diagram count — restate it or drop the pin"
    actual = len(re.findall(r"^```mermaid", _read("docs/diagrams.md"), re.M))
    assert int(claimed.group(1)) == actual, (
        f"CLAUDE.md says {claimed.group(1)} diagrams, docs/diagrams.md has {actual}")


def test_claude_md_architecture_version_matches_the_document():
    claimed = re.search(r"`docs/architecture\.md` \(v(\d+\.\d+)", _read("CLAUDE.md"))
    assert claimed, "CLAUDE.md no longer states the architecture version"
    actual = re.search(r"^\*\*Version:\*\* (\d+\.\d+)", _read("docs/architecture.md"), re.M)
    assert actual, "architecture.md has no **Version:** line to check against"
    assert claimed.group(1) == actual.group(1), (
        f"CLAUDE.md says architecture v{claimed.group(1)}, "
        f"the document says v{actual.group(1)}")


def test_the_architecture_version_line_describes_the_latest_section():
    """A version bump that does not say what it added is a version bump nobody
    can review — and the header is where a reader looks first."""
    src = _read("docs/architecture.md")
    ver = re.search(r"^\*\*Version:\*\* (\d+\.\d+)", src, re.M).group(1)
    header = src[:src.index("**Scope:**")]
    assert f"**v{ver}**" in header, \
        f"the header never explains what v{ver} added"


def test_diagrams_contents_line_reaches_the_last_diagram():
    """The contents line at the top is the only index; a diagram missing from it
    is a diagram nobody finds."""
    src = _read("docs/diagrams.md")
    numbers = [int(n) for n in re.findall(r"^## (\d+)\.", src, re.M)]
    assert numbers, "docs/diagrams.md has no numbered sections"
    contents = src[:src.index("## 1.")]
    assert str(max(numbers)) in contents, (
        f"diagram {max(numbers)} exists but the contents line stops earlier")


def test_every_numbered_diagram_is_present_exactly_once():
    numbers = [int(n) for n in re.findall(r"^## (\d+)\.", _read("docs/diagrams.md"), re.M)]
    missing = sorted(set(range(1, max(numbers) + 1)) - set(numbers))
    dupes = sorted({n for n in numbers if numbers.count(n) > 1})
    assert not missing, f"diagram numbers skipped: {missing}"
    assert not dupes, f"diagram numbers reused: {dupes}"


def test_docs_referenced_by_claude_md_exist():
    """A link to a document that was renamed sends the reader nowhere, and
    CLAUDE.md is the map every session starts from."""
    src = _read("CLAUDE.md")
    referenced = set(re.findall(r"`(docs/[A-Za-z0-9_./-]+\.md)`", src))
    assert referenced, "no doc references found — the extraction pattern broke"
    missing = sorted(p for p in referenced if not (ROOT / p).exists())
    assert not missing, f"CLAUDE.md references documents that do not exist: {missing}"
