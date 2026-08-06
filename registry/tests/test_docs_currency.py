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

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _read(rel):
    return (ROOT / rel).read_text(encoding="utf-8", errors="replace")


def test_claude_md_diagram_count_matches_the_file():
    claimed = re.search(r"`docs/diagrams\.md` \((\d+) Mermaid diagrams", _read("CLAUDE.md"))
    assert claimed, "CLAUDE.md no longer states a diagram count — restate it or drop the pin"
    actual = len(re.findall(r"^```mermaid", _read("docs/diagrams.md"), re.M))
    assert int(claimed.group(1)) == actual, (
        f"CLAUDE.md says {claimed.group(1)} diagrams, docs/diagrams.md has {actual}")


def test_claude_md_use_case_count_matches_the_document():
    """Same failure as the diagram count, and it had drifted further: CLAUDE.md
    advertised 11 use cases against a document holding 15. Seven shipped
    features were reachable only by someone who opened the file and counted."""
    claimed = re.search(r"`docs/use-cases\.md` \(TASK-ORIENTED: (\d+) end-user use cases",
                        _read("CLAUDE.md"))
    assert claimed, "CLAUDE.md no longer states a use-case count"
    actual = len(re.findall(r"^## \d+\.", _read("docs/use-cases.md"), re.M))
    assert int(claimed.group(1)) == actual, (
        f"CLAUDE.md says {claimed.group(1)} use cases, docs/use-cases.md has {actual}")


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


def test_architecture_current_estate_marker_matches_the_registry():
    """Historical rollout counts may remain, but the current-estate marker
    must agree with the registry that drives runtime discovery."""
    registry = yaml.safe_load(_read("registry/repo-registry.yaml"))
    sources = registry.get("source_repositories", [])
    tests = registry.get("test_repositories", [])
    api = sum(repo.get("layer") == "api" for repo in tests)
    ui = sum(repo.get("layer") == "ui" for repo in tests)
    marker = re.search(
        r"checked-in reference estate.*?declares \*\*(\d+) source repositories "
        r"and (\d+) test repositories \((\d+) API, (\d+) UI\)\*\*",
        _read("docs/architecture.md"), re.I,
    )
    assert marker, "architecture.md has no parseable current-estate marker"
    assert tuple(map(int, marker.groups())) == (len(sources), len(tests), api, ui)


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


def test_the_generated_agents_md_points_coding_agents_at_their_guide():
    """Root AGENTS.md is estate knowledge for the test-authoring phases and is
    rewritten by every pipeline run. Coding agents look for that filename and
    find the wrong document — so the generator emits one line pointing at the
    guide, and the guide has to exist for the pointer to be worth anything."""
    agents = _read("AGENTS.md")
    assert "docs/coding-agent-guide.md" in agents, \
        "the generated AGENTS.md no longer points coding agents anywhere"
    guide = ROOT / "docs/coding-agent-guide.md"
    assert guide.exists(), "the pointer names a guide that does not exist"
    text = guide.read_text(encoding="utf-8").lower()
    assert "auto-generated" in text, \
        "the guide must warn that root AGENTS.md is generated, or someone edits it"
    assert "make review" in text, \
        "the guide must tell an agent how to verify its work"


# --- the dashboard view count -------------------------------------------------
# Six documents stated it, and at the last docs review FIVE were wrong: nine,
# ten, eleven, eleven and fourteen against a real fifteen. Each was correct when
# written; every view added since made all of them a little more wrong, and
# nothing failed. A reader who counts fifteen against a doc claiming nine does
# not conclude the doc is stale — they conclude they are looking at the wrong
# thing.
_NUMBER_WORDS = {
    "nine": 9, "ten": 10, "eleven": 11, "twelve": 12, "thirteen": 13,
    "fourteen": 14, "fifteen": 15, "sixteen": 16, "seventeen": 17,
    "eighteen": 18, "nineteen": 19, "twenty": 20,
}
# Only a claim ABOUT the set of views: "<n> views", "<n>-view app/SPA/dashboard".
# "this view", "in-view" and "per-view" are not counts and must not match.
_VIEW_CLAIM = re.compile(
    r"\b(\d+|" + "|".join(_NUMBER_WORDS) + r")[- ]"
    r"(?:views\b|view (?:app|SPA|dashboard|QA UI)\b)",
    re.I)


def _view_claims():
    """Every stated view count in the user-facing docs, as (file, line, n)."""
    files = sorted(ROOT.joinpath("docs").rglob("*.md"))
    files += [ROOT / "docs/demo-deck.html", ROOT / "README.md", ROOT / "REVIEW.md"]
    for p in files:
        if not p.exists():
            continue
        for i, line in enumerate(p.read_text(encoding="utf-8", errors="replace")
                                 .splitlines(), 1):
            for m in _VIEW_CLAIM.finditer(line):
                tok = m.group(1).lower()
                yield (p.relative_to(ROOT).as_posix(), i,
                       int(tok) if tok.isdigit() else _NUMBER_WORDS[tok])


def test_every_stated_view_count_matches_the_dashboard():
    actual = len(set(re.findall(r'data-view="([a-z-]+)"', _read("bin/dashboard.py"))))
    assert actual, "no data-view attributes found — the extraction pattern broke"
    claims = list(_view_claims())
    assert claims, "no document states a view count — the pattern broke, not the docs"
    wrong = [(f, ln, n) for f, ln, n in claims if n != actual]
    assert not wrong, (
        f"the dashboard has {actual} views; these say otherwise: "
        + ", ".join(f"{f}:{ln} says {n}" for f, ln, n in wrong))


def test_readme_roadmap_shipped_count_matches_the_roadmap_summary():
    """The README said 12 shipped while the roadmap said 14. Both statements
    looked authoritative; neither named that two deliveries were only partial."""
    readme = re.search(
        r"(\d+) items are fully shipped and (\d+) are partially shipped",
        _read("README.md"), re.I,
    )
    roadmap_text = _read("docs/product-roadmap.md")
    roadmap = re.search(
        r"(\w+) are fully shipped and (\w+) are partially shipped",
        roadmap_text, re.I,
    )
    assert readme and roadmap, "the shipped-count summaries are no longer parseable"
    count_words = {
        "zero": 0, "one": 1, "two": 2, "three": 3, "four": 4,
        "five": 5, "six": 6, "seven": 7, "eight": 8,
        **_NUMBER_WORDS,
    }
    full = count_words.get(roadmap.group(1).lower())
    partial = count_words.get(roadmap.group(2).lower())
    assert full is not None and partial is not None
    section = roadmap_text.split("## 0a.", 1)[1].split("\n## 1.", 1)[0]
    rows = re.findall(r"^\|\s*\d+\.\d+\s*\|.*$", section, re.M)
    computed_partial = sum("(partial)" in row.lower() for row in rows)
    computed_full = len(rows) - computed_partial
    assert (full, partial) == (computed_full, computed_partial)
    assert (int(readme.group(1)), int(readme.group(2))) == (full, partial)


def test_no_document_contains_an_empty_code_block():
    """An opening fence immediately followed by its closing fence renders as a
    blank grey box. deployment.md carried one directly under the teardown
    instructions, where a reader looks for the command — an empty box there
    reads as 'the command was removed', not 'somebody left a hole'."""
    empty = []
    for p in sorted(ROOT.joinpath("docs").rglob("*.md")) + [ROOT / "README.md"]:
        lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        for i, ln in enumerate(lines):
            if (ln.startswith("```") and ln.strip() != "```"
                    and i + 1 < len(lines) and lines[i + 1].strip() == "```"):
                empty.append(f"{p.relative_to(ROOT).as_posix()}:{i + 1}")
    assert not empty, f"empty code blocks: {empty}"


def test_the_stated_adversarial_suite_count_matches_make_review():
    """Two documents told an agent `make review` runs six adversarial suites when
    it runs seven. Under-counting here is the bad direction: it is the number a
    reader uses to decide whether the output they got was the whole run."""
    review = re.search(r"^review:\n(.*?)(?=\n[a-z])", _read("Makefile"), re.S | re.M)
    assert review, "the review target could not be parsed"
    actual = len(set(re.findall(r"bash (tests/[\w.-]+\.sh)", review.group(1))))
    assert actual, "make review runs no tests/*.sh — the pattern broke"
    # Only the documents that tell a reader what `make review` runs TODAY.
    # CLAUDE.md is excluded on purpose: its count sits inside a record of a
    # measurement taken when six suites existed, and "correcting" a historical
    # measurement to today's number would make it a false claim about the past.
    claims = []
    for rel in ("docs/user-guide.md", "docs/coding-agent-guide.md", "README.md"):
        for m in re.finditer(r"\b(\w+)\s+adversarial(?:/smoke)?\s+suites", _read(rel)):
            tok = m.group(1).lower()
            n = int(tok) if tok.isdigit() else _NUMBER_WORDS.get(
                tok, {"six": 6, "seven": 7, "five": 5, "four": 4, "eight": 8}.get(tok))
            if n is not None:
                claims.append((rel, n))
    wrong = [(f, n) for f, n in claims if n != actual]
    assert not wrong, (f"make review runs {actual} suites from tests/; "
                       + ", ".join(f"{f} says {n}" for f, n in wrong))


def test_every_make_target_is_documented_somewhere():
    """A feature that ships with no way to find it has not really shipped.

    This is the cheapest possible check for the thing that actually goes wrong:
    a target is added to the Makefile during a build slice and the docs are
    updated for the FEATURE but never for the command. Eight had accumulated
    that way — including `make test-entrypoint`, the suite guarding whether a
    new deployment has an estate at all.
    """
    makefile = _read("Makefile")
    targets = sorted(set(re.findall(r"^([a-z][a-z0-9-]+):", makefile, re.M)))
    assert len(targets) > 40, f"only {len(targets)} targets parsed — the pattern broke"
    docs = "\n".join(p.read_text(encoding="utf-8", errors="replace")
                     for p in sorted(ROOT.joinpath("docs").rglob("*.md")))
    missing = [t for t in targets
               if not re.search(rf"(make {re.escape(t)}\b|`{re.escape(t)}`)", docs)]
    assert not missing, f"make targets no document mentions: {missing}"
