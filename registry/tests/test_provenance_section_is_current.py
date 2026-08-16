"""§5.23 must keep describing the code, not a design that used to be true.

The design doc has been caught twice in this session describing something the
code does not do: it drew the alert evaluator being told about a degraded log
by a flag that cannot cross processes, and it never mentioned the signing
mechanism two enforcement points now verify. Both were found by reading, and
CLAUDE.md already records the cost of the general case - a stale design doc
taught `restrict_test_repos`, a routing key nothing reads.

So this section, which is the reference a reader follows when they add a SIXTH
signal, is pinned against the code rather than left to prose review. The pins
assert the CLAIMS - the named decision functions exist, the deliberate
non-change is still deliberate, the version is in step - not the wording.
"""
import pathlib
import sys

import pytest
import yaml

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))

DOC = ROOT / "docs/architecture.md"


def _section():
    t = DOC.read_text(encoding="utf-8")
    i = t.index("### 5.23 Provenance")
    return t[i:t.index("## 6.", i)]


# (module, attribute) named by the section as the one decision function for a
# signal. A section naming a function nobody can import is the shape this pin
# exists to catch.
DECISION_FUNCTIONS = [
    ("phase_provenance", "of"),
    ("critic", "provenance"),
    ("test_reviewer", "verdict_text"),
    ("plan_state", "linked_cell"),
    ("delivery", "outcome"),
]


@pytest.mark.parametrize("mod,attr", DECISION_FUNCTIONS)
def test_every_decision_function_the_section_names_exists(mod, attr):
    section = _section()
    assert f"{mod}.{attr}" in section, \
        f"§5.23 no longer names {mod}.{attr} as a signal's decision function"
    m = __import__(mod)
    assert hasattr(m, attr), \
        f"§5.23 names {mod}.{attr} and it does not exist"


def _signal_rows():
    """The first column of the signal table - the canonical list.

    Scoped to the TABLE on purpose: a section-wide search for "delivery" passes
    while the table row is gone, because the word appears in the prose and in
    `delivery.outcome` two paragraphs down. That mutation survived the first
    pass, which is the same file-level-check weakness this repo records for the
    fifth critic renderer.
    """
    rows = []
    for line in _section().splitlines():
        line = line.strip()
        if line.startswith("|") and line.count("|") >= 4 and "---" not in line:
            cell = line.split("|")[1].strip().lower()
            if cell and cell != "signal":
                rows.append(cell)
    return rows


def test_the_signal_table_lists_all_five():
    """The whole point of the amendment: the rule is not about money."""
    rows = _signal_rows()
    for signal in ("spend", "critic score", "validation counts",
                   "reviewer verdict", "delivery"):
        assert signal in rows, \
            f"the §5.23 signal table dropped {signal!r}; it lists {rows}"
    assert len(rows) == 5, f"the table gained or lost a row: {rows}"


def test_the_delivery_states_match_the_module():
    import delivery
    s = _section()
    assert "sent | simulated | failed" in s, \
        "§5.23 no longer states delivery's three states"
    for state in (delivery.SENT, delivery.SIMULATED, delivery.FAILED):
        assert state in s
    assert "only `sent` may advance state" in s


def test_the_deliberate_non_change_is_still_deliberate():
    """If alert_rules' cooldown ever DOES gate on a real send, this paragraph
    becomes a lie in the direction that matters - a reader would conclude the
    platform is less careful than it is."""
    s = _section()
    assert "cooldown" in s and "alert_rules" in s
    src = (ROOT / "engine/lib/alert_rules.py").read_text(encoding="utf-8")
    assert "labelling fix here, not a state-machine change" in src, \
        "alert_rules no longer records why its cooldown is left alone; §5.23 " \
        "still says it is deliberate"


def test_the_two_alarms_it_names_really_gate_on_delivery():
    """The section's sharpest claim: two alarms advance durable state on
    delivery. If either stopped doing so the paragraph would misdescribe the
    risk it exists to explain."""
    s = _section()
    assert "coverage_drift" in s and "spec_drift" in s
    for rel in ("engine/lib/coverage_drift.py", "engine/lib/spec_drift.py"):
        src = (ROOT / rel).read_text(encoding="utf-8")
        assert "delivery.landed(" in src, \
            f"{rel} no longer gates on delivery; §5.23 says it does"


def test_the_section_is_paired_with_c13_not_confused_with_it():
    s = _section()
    assert "5.19" in s, "§5.23 no longer points at its neighbour"
    assert "C13" in s and "C10" in s


def test_the_clause_and_the_section_agree_on_the_signal_list():
    """Two documents over one fact is how this repo's own findings start. The
    constitution is the normative copy; the section explains it."""
    doc = yaml.safe_load(
        (ROOT / "specs/platform/constitution.yaml").read_text(encoding="utf-8"))
    c10 = next(c for c in doc["clauses"] if c["id"] == "C10")["statement"].lower()
    s = _section().lower()
    for signal in ("critic", "validation", "reviewer", "delivery"):
        assert signal in c10 and signal in s, \
            f"C10 and §5.23 disagree about the {signal} signal"


def test_the_version_marker_advanced_with_the_section():
    """The currency pin already ties CLAUDE.md to this version; this asserts
    the section itself carries the version it was added in, so a reader can
    tell how old the description is."""
    t = DOC.read_text(encoding="utf-8")
    assert "**Version:** 2.11" in t
    assert "(v2.11)" in _section().split("\n")[0]
