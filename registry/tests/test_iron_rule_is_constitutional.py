"""The most-enforced honesty rule in this codebase was constitutionally scoped
to money.

FOUND BY ASKING an architecture question rather than driving a command: this
session established the "iron rule" across FIVE signals a mock can fabricate -
spend, the critic score, validation counts, the reviewer verdict, and delivery
to an external system - each with its own pins. Which of them does the
machine-readable constitution actually name?

MY FIRST HYPOTHESIS WAS WRONG and the correction is the finding. I expected the
rule to be absent entirely; it is present as C10, but stated as "a simulated
COST figure can never masquerade as a measured dollar" and pinned by two cost
tests. Measured against the seven provenance pin files in the tree, NONE was
named by any clause.

Two consequences, both of them the repo's own stated standard turned on itself:

  * `engine/lib/governance_page.py` renders the constitution as the shareable
    "how we build E2E tests here" document, and it told a reader the honesty
    guarantee was about money.
  * `test_constitution.py` breaks the build when a NAMED pin disappears - the
    mechanism that stops a rule quietly becoming undefended. It was watching
    two cost pins and none of the other four signals.

C10 is WIDENED rather than duplicated by a new clause: it is one rule, and two
clauses stating it would be the two-surfaces-one-fact problem this codebase
keeps fixing.

WHY C10 AND C13 BOTH EXIST, since they are neighbours: C13 is about a fact the
platform could not ESTABLISH. Here the fact is perfectly well established - it
is just a fact about the STUB rather than about the product.
"""
import pathlib
import sys

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))

CONSTITUTION = ROOT / "specs/platform/constitution.yaml"

# The signals a mock can fabricate, and a pin file that defends each. Adding a
# sixth signal to the product means adding it here, which is the point.
SIGNALS = {
    "spend": "registry/tests/test_scorecard_honesty.py",
    "critic score": "registry/tests/test_critic_score_provenance.py",
    "validation counts": "registry/tests/test_validation_counts_provenance.py",
    "delivery": "registry/tests/test_delivery_provenance.py",
    "alarm state": "registry/tests/test_simulated_delivery_advances_nothing.py",
}


def _c10():
    doc = yaml.safe_load(CONSTITUTION.read_text(encoding="utf-8"))
    for c in doc["clauses"]:
        if c["id"] == "C10":
            return c
    raise AssertionError("C10 is gone from the constitution")


def test_the_clause_states_the_rule_for_every_signal_not_only_money():
    """THE DEFECT. A clause narrower than the rule it names leaves the rest
    undefended by the very mechanism that exists to notice that."""
    s = _c10()["statement"].lower()
    for word in ("critic", "validation", "reviewer", "delivery"):
        assert word in s, \
            f"C10 no longer covers the {word} signal; it has been narrowed " \
            f"back towards cost-only"
    assert "simulated" in s


def test_every_signal_has_a_pin_the_constitution_names():
    """THE INVARIANT, not today's pin list: each signal must be watched by
    `test_constitution.py`, or deleting its pins breaks no build."""
    named = {p["file"] for p in _c10()["pins"]}
    for signal, f in SIGNALS.items():
        assert f in named, \
            f"the {signal} signal is enforced by {f}, which no clause names"


@pytest.mark.parametrize("f", sorted(set(SIGNALS.values())))
def test_each_named_pin_file_exists(f):
    """`test_constitution.py` checks this too; asserted here so a rename shows
    up as "this signal lost its pin" rather than a generic clause error."""
    assert (ROOT / f).exists(), f


def test_the_clause_keeps_the_unknown_state():
    """The direction that took three iterations to get right elsewhere: an
    unrecorded provenance is UNKNOWN, and stamping it "real" is the lie."""
    s = _c10()["statement"].lower()
    assert "unknown" in s and "never" in s


def test_the_statement_is_a_rule_not_a_changelog():
    """The governance page renders this verbatim to a shareable audience. My
    first draft embedded the history of the amendment in the clause itself;
    a constitution states what holds, not how it came to."""
    s = _c10()["statement"]
    for leak in ("was scoped", "broke no build", "understated"):
        assert leak not in s, \
            f"C10's statement carries commentary ({leak!r}) that belongs in " \
            f"the change record, not in the rule"


def test_the_human_rendering_lists_it():
    """CLAUDE.md's Non-negotiables section is the human copy of this file, and
    it did not mention the iron rule at all - so the one document every agent
    reads first omitted the invariant most of them touch."""
    md = (ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    i = md.index("## Non-negotiables")
    block = md[i:i + 6000]
    assert "(C10" in block, "the human rendering does not list C10"
    assert "iron rule" in block.lower()


def test_c10_and_c13_stay_distinct():
    """They are neighbours and collapsing them would lose the difference that
    makes each actionable: C13 is "we could not establish this", C10 is "this
    is established, about the stub"."""
    doc = yaml.safe_load(CONSTITUTION.read_text(encoding="utf-8"))
    by_id = {c["id"]: c for c in doc["clauses"]}
    assert "C13" in by_id, "C13 is gone"
    c10, c13 = by_id["C10"]["statement"], by_id["C13"]["statement"]
    assert c10 != c13
    assert "inability to establish" in c13.lower()
    assert "inability to establish" not in c10.lower(), \
        "C10 has been reworded into a restatement of C13"


def test_the_governance_page_carries_the_widened_rule():
    """DRIVEN: the shareable document is the whole reason this clause's WORDING
    matters, and a pin on the yaml alone would not prove it reaches a reader."""
    import governance_page
    md = governance_page.render() if hasattr(governance_page, "render") \
        else governance_page.markdown()
    assert "C10" in md
    i = md.index("C10")
    block = md[i:i + 1800]
    assert "critic score" in block and "delivery" in block, block[:400]
    for signal, f in SIGNALS.items():
        assert f in block, f"the governance page does not show {signal}'s pin"
