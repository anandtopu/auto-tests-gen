"""Every resolution answers the same shape, whichever branch produced it.

`resolve_pr`'s main return already said so in a comment — "an empty list, not
a missing key, so every consumer reads one shape" — and MEASURED, four returns
disagreed:

    unregistered repo : no skip, no uncovered_sources, no unknown_label_rules
    empty change list : skip + empty_change_list, none of the source fields
    nothing testable  : same
    the routed answer : the source fields, but no skip

Nothing crashed, because every consumer happens to use `.get()`. That is
precisely how a field teaches the next reader it "only sometimes exists" — and
the next consumer to index one directly gets a KeyError on a branch nobody
tested. Same reasoning as R4, already recorded here: a guard present in one
branch and absent from its sibling is a guard that gets lost.

The fix is a defaults dict, so a new branch inherits the shape instead of
having to remember it. These pins assert the INVARIANT (all branches agree)
rather than today's key list, so adding a legitimate new field does not break
them — only letting the branches diverge again does.
"""
import json
import os
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/phases"))
sys.path.insert(0, str(ROOT / "engine/lib"))


def _resolve(*args):
    r = subprocess.run([sys.executable, str(ROOT / "engine/phases/resolve.py"), *args],
                       cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace",
                       stdin=subprocess.DEVNULL,
                       env={**os.environ, "AIQE_MOCK": "1"})
    assert r.returncode == 0, r.stderr[-800:]
    return json.loads(r.stdout)


BRANCHES = [
    pytest.param(("pr", "zz-not-a-registered-repo"), id="unregistered"),
    pytest.param(("pr", "orders-api"), id="routed-or-skipped"),
    pytest.param(("jira", "PROJ-301", "--components", "Catalog"), id="jira"),
]


def test_every_branch_returns_the_same_keys():
    """THE INVARIANT. Asserted across branches rather than against a fixed
    list, so a legitimately new field does not break this pin."""
    shapes = {}
    for params in BRANCHES:
        args = params.values[0]
        shapes[params.id] = tuple(sorted(_resolve(*args)))
    distinct = set(shapes.values())
    assert len(distinct) == 1, \
        "resolution branches disagree about their shape:\n" + \
        "\n".join(f"  {k}: {v}" for k, v in shapes.items())


@pytest.mark.parametrize("params", BRANCHES)
def test_the_fields_a_consumer_reads_are_always_present(params):
    """The specific keys whose absence was measured. A consumer that indexes
    one directly must not depend on which branch answered."""
    out = _resolve(*params)
    for key in ("skip", "empty_change_list", "uncovered_sources",
                "layer_filtered_sources", "unknown_label_rules",
                "source_repos", "test_repos", "confidence", "rationale"):
        assert key in out, f"{key} missing from this branch"


def test_the_defaults_are_empty_not_absent():
    """An unregistered repo establishes nothing, so the source lists must be
    EMPTY rather than missing — and `skip` false rather than unset."""
    out = _resolve("pr", "zz-not-a-registered-repo")
    assert out["uncovered_sources"] == []
    assert out["layer_filtered_sources"] == []
    assert out["unknown_label_rules"] == []


def test_an_explicit_value_still_wins_over_the_default():
    """The defaults must not overwrite what a branch actually computed — the
    obvious way to break this fix."""
    import resolve as resolve_mod
    got = resolve_mod._resolution(skip=True, uncovered_sources=["catalog-api"])
    assert got["skip"] is True
    assert got["uncovered_sources"] == ["catalog-api"]
    assert got["unknown_label_rules"] == []


def test_the_defaults_are_not_shared_between_resolutions():
    """A module-level dict handed out by reference would let one resolution's
    list mutate the next one's."""
    import resolve as resolve_mod
    a = resolve_mod._resolution()
    a["uncovered_sources"].append("leaked")
    b = resolve_mod._resolution()
    assert b["uncovered_sources"] == [], "resolutions share mutable state"


def test_the_skip_branches_still_skip():
    """The behaviour under the shape must be untouched: an empty change list
    still skips, and still says confidence 0.0 because nothing was seen."""
    out = _resolve("pr", "zz-not-a-registered-repo")
    assert out["confidence"] == 0.0
