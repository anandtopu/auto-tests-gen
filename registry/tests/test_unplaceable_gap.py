"""A gap you cannot place is not a gap you should be told to prioritize.

`make gaps` ended every uncovered surface with the same sentence:

    <- coverage gap: prioritize a scenario here

MEASURED on the shipped registry, that advice is impossible for one of the three
repos it was given for. `catalog-api` has a real gap at `/v1/catalog/search` and
NO test repo covers it, so a scenario written against it would be generated
nowhere -- the run resolves no test repo and produces nothing. The operator
follows the report, queues the work, and gets silence.

It matters more than a wording nit for two reasons. `out/coverage-gaps.md` is
injected as context into triage, testplan, generate and the plan adversary, so
the MODEL is told to prioritize something it cannot place. And the fix differs
from the ordinary case: an ordinary gap is closed by writing a scenario, while
this one is closed by giving the repo a test repo first (onboard one, or add it
to an existing repo's `scope`). Same sentence, two different actions -- C13.

This is the planning-time half of the same fact `uncovered_note` reports at
routing time. The report is what a human reads BEFORE queueing the work; the
routing note is what they see after. Both now name it.
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))

import coverage_gaps                                             # noqa: E402
# Aliased deliberately: pytest COLLECTS any module-level name starting with
# `test_`, including an imported function, and reported `test_repos_for` as
# an erroring test.
from registry import load_registry                               # noqa: E402
from registry import test_repos_for as covering_repos            # noqa: E402

PLACEABLE = "prioritize a scenario here"
UNPLACEABLE = "generated NOWHERE"


def _sections(md):
    """{repo: its block} from the rendered report."""
    out, name, buf = {}, None, []
    for line in md.splitlines():
        if line.startswith("## "):
            if name:
                out[name] = "\n".join(buf)
            name, buf = line[3:].split(" (")[0].strip(), []
        elif name:
            buf.append(line)
    if name:
        out[name] = "\n".join(buf)
    return out


def test_an_unplaceable_gap_names_the_fix_that_would_work():
    reg = load_registry()
    md = coverage_gaps.to_markdown()
    blocks = _sections(md)
    checked = 0
    for repo, block in blocks.items():
        if "[NO TEST]" not in block:
            continue
        if covering_repos(reg, repo):
            continue
        checked += 1
        assert UNPLACEABLE in block, \
            f"{repo} has gaps and no test repo, but is told to write a scenario"
        assert "scope" in block or "onboard" in block, \
            f"{repo}'s advice does not name the fix that would actually work"
        # The per-line advice and the block's summary NOTE are separate
        # renderings and a mutation pass showed each survived without the
        # other pinned. Someone scanning the block reads the NOTE, not the
        # tail of every gap line.
        assert "unplaceable until" in block, \
            f"{repo}'s gaps carry no summary NOTE"
    assert checked, ("no repo in this estate has an unplaceable gap, so this "
                     "pin proved nothing -- check the fixture registry")


def test_a_placeable_gap_keeps_the_ordinary_advice():
    """The over-fix guard. Most gaps ARE actionable, and burying them in a
    caveat is how the caveat stops meaning anything."""
    reg = load_registry()
    blocks = _sections(coverage_gaps.to_markdown())
    checked = 0
    for repo, block in blocks.items():
        if "[NO TEST]" not in block or not covering_repos(reg, repo):
            continue
        checked += 1
        assert PLACEABLE in block, f"{repo}'s ordinary gap lost its advice"
        assert UNPLACEABLE not in block, \
            f"{repo} is covered, so its gaps must not be called unplaceable"
        assert "unplaceable" not in block, \
            f"{repo} is covered but carries the unplaceable NOTE"
    assert checked, "no placeable gap in this estate -- the control proved nothing"


def test_the_two_situations_do_not_render_alike():
    """The property that was violated, stated directly."""
    reg = load_registry()
    blocks = _sections(coverage_gaps.to_markdown())
    placeable = {r for r, b in blocks.items()
                 if "[NO TEST]" in b and covering_repos(reg, r)}
    unplaceable = {r for r, b in blocks.items()
                   if "[NO TEST]" in b and not covering_repos(reg, r)}
    assert placeable and unplaceable, "the estate no longer exercises both"
    a = blocks[sorted(placeable)[0]]
    b = blocks[sorted(unplaceable)[0]]
    assert a.split("<-")[1][:40] != b.split("<-")[1][:40], \
        "a gap that can be filled and one that cannot read identically again"


def test_a_covered_repo_with_no_gaps_gets_no_note():
    """A note that fires where there is nothing to say is one readers skip."""
    md = coverage_gaps.to_markdown()
    for repo, block in _sections(md).items():
        if "[NO TEST]" not in block:
            assert "unplaceable" not in block, \
                f"{repo} has no gaps but carries the unplaceable note"


def _boom(*a, **k):
    raise KeyError("test_repositories")


def test_a_registry_we_could_not_read_claims_neither_answer(monkeypatch):
    """The third state, and it is not a convenience. A coverage lookup that
    fails establishes NOTHING -- rendering the unplaceable text would send
    someone to onboard a test repo that may already exist, and rendering the
    ordinary text is the impossible advice this fix removed. C13.
    """
    monkeypatch.setattr(coverage_gaps, "test_repos_for", _boom)
    md = coverage_gaps.to_markdown()
    assert "[NO TEST]" in md, "the estate no longer renders a gap to check"
    assert "could not be established" in md
    assert UNPLACEABLE not in md
    assert PLACEABLE not in md
    # The NOTE is a SECOND assertion of the same claim and carries none of the
    # advice text, so checking the advice alone let it through.
    assert "unplaceable" not in md, \
        "a coverage lookup that failed still rendered the accusatory NOTE"


def test_an_unreadable_registry_does_not_take_down_the_report(monkeypatch):
    """One malformed record taking down a whole surface is a class this repo
    has already been bitten by (the dashboard). `make gaps` degrades."""
    monkeypatch.setattr(coverage_gaps, "test_repos_for", _boom)
    md = coverage_gaps.to_markdown()
    assert md.startswith("# Coverage gaps")
    assert len(_sections(md)) > 1


def test_the_report_still_separates_what_it_could_not_examine():
    """The earlier fix must survive this one: 'we could not look' is a third
    state, distinct from both kinds of gap."""
    md = coverage_gaps.to_markdown()
    assert "NOT checked" in md
    assert "known to be gap-free" in md
