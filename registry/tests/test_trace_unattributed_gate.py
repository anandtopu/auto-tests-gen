"""A committed test must not read as one that was never committed.

The trace matrix is the table CLAUDE.md calls what "regulated teams ask for",
and its loudest line is meant to be an approved scenario nothing exercises.
MEASURED on the estate via `trace_matrix.build()`, two opposite findings
rendered identically:

    PR-orders-api-201   file=yes  repo=(none)  gate=(empty)   <- committed
    PROJ-301-S2         file=no   repo=(none)  gate=(empty)   <- nothing exists

The first row's spec IS in the repository -- the run records carry the sha.
Its run gated TWO repositories and the generate contract did not stamp `repo`
on the test, so `_row()` refuses to guess an owner. That refusal is correct and
stays: inventing a cross-repo link in an audit table would be worse. What was
missing is that the refusal said nothing, so "we could not establish the owner"
and "this was never committed" shared one empty cell (C13).

`unattributed` is now its own state. It is deliberately NOT counted with the
uncovered rows: those tests exist, and the unknown is which repository owns
them, not whether anyone wrote them.
"""
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))

import trace_matrix                                            # noqa: E402


def _record(gates, tests, key="PR-x-1"):
    return {"run_id": "r1", "ts": 10, "trigger": {"type": "pr", "key": key},
            "phases": [{"name": "generate", "contract": {"tests": tests}}],
            "gates": gates}


def _rows(monkeypatch, record):
    """No tmp_path: `_run_records` is patched, so a run record written to disk
    would never be read. The first version wrote one anyway and inherited a
    dependency on pytest's temp root, which this host intermittently refuses
    to create."""
    monkeypatch.setattr(trace_matrix, "_run_records", lambda: [record])
    return trace_matrix.build()


TWO_GATES = [{"test_repo": "e2e-api-tests-1", "status": "committed",
              "commit": "abc1234def"},
             {"test_repo": "e2e-ui-tests-1", "status": "no_changes"}]
ONE_GATE = [{"test_repo": "e2e-api-tests-1", "status": "committed",
             "commit": "abc1234def"}]
UNSTAMPED = [{"file": "suites/orders/a.spec.js", "action": "created"}]


def test_an_unattributable_committed_test_says_so(monkeypatch):
    """THE DEFECT: this row rendered exactly as loud as a scenario with no
    test at all."""
    rows = _rows(monkeypatch, _record(TWO_GATES, UNSTAMPED))
    assert len(rows) == 1
    assert rows[0]["file"], "the fixture lost its test"
    assert rows[0]["gate_status"] == "unattributed"


def test_a_scenario_with_no_test_stays_empty(monkeypatch):
    """The other direction. An uncovered scenario must NOT acquire a state
    that suggests a test exists somewhere -- that would trade a quiet lie for
    a loud one, in the row an audit exists to find."""
    rows = _rows(monkeypatch, _record(TWO_GATES, []))
    assert [r["gate_status"] for r in rows] == [""] or not rows


def test_an_attributable_test_still_reports_the_real_gate(monkeypatch):
    """One gate makes the owner unambiguous, and that path must be untouched:
    a genuinely committed test still says `committed` with its sha."""
    rows = _rows(monkeypatch, _record(ONE_GATE, UNSTAMPED))
    assert rows[0]["gate_status"] == "committed"
    assert rows[0]["commit"] == "abc1234de"
    assert rows[0]["test_repo"] == "e2e-api-tests-1"


def test_a_stamped_test_is_attributed_even_with_several_gates(monkeypatch):
    """The stamp is the whole point of the fan-out contract: when it is there,
    several gates are not ambiguous at all."""
    stamped = [{"file": "suites/orders/a.spec.js", "action": "created",
                "repo": "e2e-api-tests-1"}]
    rows = _rows(monkeypatch, _record(TWO_GATES, stamped))
    assert rows[0]["gate_status"] == "committed"


def test_the_report_explains_the_word(monkeypatch):
    """`unattributed` in a column is jargon until something says what it
    means, and an auditor reading a bare word will assume the worst."""
    rows = _rows(monkeypatch, _record(TWO_GATES, UNSTAMPED))
    text = "\n".join(trace_matrix.render_text(rows))
    assert "UNATTRIBUTED" in text
    assert "not 'never committed'" in text
    assert "suites/orders/a.spec.js" in text


def test_unattributed_rows_are_not_counted_as_uncovered(monkeypatch):
    """The count an audit opens with. These tests EXIST; folding them into the
    uncovered total would overstate the gap and contradict the row itself."""
    rows = _rows(monkeypatch, _record(TWO_GATES, UNSTAMPED))
    text = "\n".join(trace_matrix.render_text(rows))
    assert "APPROVED SCENARIO WITH NO TEST" not in text
    assert "every approved scenario has a test" in text


def test_the_gate_column_is_wide_enough_for_its_longest_value(monkeypatch):
    """A value that overflows its field pushes every later column out of
    alignment, which in a fixed-width audit table is how a reader ends up
    reading the wrong cell."""
    rows = _rows(monkeypatch, _record(TWO_GATES, UNSTAMPED))
    lines = trace_matrix.render_text(rows)
    header, row = lines[0], lines[1]
    assert header.index("ci") == row.index("-", header.index("ci") - 2), \
        "the ci column no longer lines up with its header"


def test_the_csv_carries_the_state_too():
    """The downloaded artifact is what an audit keeps; a state visible only in
    the terminal is not evidence."""
    rows = [{"key": "K", "scenario_id": "", "scenario_title": "", "behavior_ref": "",
             "requirements": "", "file": "a.spec.js", "test_repo": "",
             "action": "created", "gate_status": "unattributed", "commit": "",
             "run_id": "r1", "ci_runs": "", "ci_failures": "", "ci_last": "",
             "reused_from": "", "plan_status": "", "waiver": ""}]
    assert "unattributed" in trace_matrix.to_csv(rows)
