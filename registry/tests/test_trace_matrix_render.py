"""The traceability table an auditor reads is labelled, and counts what matters.

Found by running `make trace-matrix`. It printed five unlabelled columns --
key, scenario, file, gate, ci -- so a reader could not tell whether "committed"
was the gate's verdict or CI's. The CSV form has carried a header all along,
and the sibling commands (`qa.py reviews`, `qa.py coverage`) both label theirs;
this one was the odd one out.

The second half matters more. Rows with no test carry a per-row marker, which
makes them findable but not countable -- and "how many approved scenarios have
no test?" is the question an audit opens with. There is now a summary line, and
it speaks in BOTH directions: when everything is covered it says so, rather than
falling silent and leaving the reader to infer good news from an absence.
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
import trace_matrix  # noqa: E402


def _row(key="PROJ-1", sid="S1", file="", gate=""):
    return {"key": key, "scenario_id": sid, "file": file, "gate_status": gate,
            "ci_last": ""}


def test_the_table_labels_its_columns():
    out = trace_matrix.render_text([_row(file="suites/a.spec.js", gate="committed")])
    header = out[0]
    for col in ("key", "scenario", "test file", "gate", "ci"):
        assert col in header, f"the {col!r} column is unlabelled"


def test_uncovered_scenarios_are_counted_and_named():
    """A per-row marker makes them findable; it does not make them countable."""
    rows = [_row(sid="S1", file="suites/a.spec.js", gate="committed"),
            _row(sid="S2"), _row(sid="S3")]
    out = "\n".join(trace_matrix.render_text(rows))
    assert "2 of 3" in out, "the uncovered count is missing"
    assert "S2" in out.split("2 of 3")[1] and "S3" in out.split("2 of 3")[1], \
        "the summary does not name which scenarios are uncovered"


def test_full_coverage_is_stated_rather_than_left_silent():
    """Silence reads as 'the report did not check', which is the C13 shape."""
    rows = [_row(sid="S1", file="suites/a.spec.js", gate="committed")]
    out = "\n".join(trace_matrix.render_text(rows))
    assert "every approved scenario has a test" in out
    assert "WITH NO TEST" not in out


def test_an_empty_matrix_says_so_and_prints_no_header():
    """A header over nothing looks like a table that lost its rows."""
    out = trace_matrix.render_text([])
    assert out == ["no traceable runs yet"], out


def test_every_uncovered_row_still_carries_its_marker():
    """The summary is an addition, not a replacement -- the row-level marker is
    what makes the offending line findable in a long table."""
    out = "\n".join(trace_matrix.render_text([_row(sid="S9")]))
    # The MARKER, not a particular wording: the label now depends on whether
    # the plan was approved, and this fixture carries no plan status.
    assert "<-" in out and "WITH NO TEST" in out


def test_the_csv_form_is_untouched_by_the_text_rendering():
    """Machine consumers (the dashboard, ?format=csv) must not inherit a
    human summary line."""
    csv_out = trace_matrix.to_csv([
        {f: "" for f in trace_matrix.FIELDS} | {"key": "K", "file": "f.js"}])
    assert csv_out.splitlines()[0].startswith("key,scenario_id,")
    assert "APPROVED SCENARIO" not in csv_out
    assert "row(s)" not in csv_out


# --- approval is a fact, not a label ----------------------------------------
#
# The contract snapshot this matrix reads is written when a plan is DRAFTED
# (pipeline.sh plan stops after testplan and marks it draft). Labelling every
# uncovered row "APPROVED SCENARIO" therefore asserted a sign-off that may never
# have happened -- on the one artifact regulated teams read.
#
# Measured on this estate when it was found: PROJ-301 was status=draft with no
# approval in history, and `make trace-matrix` called all three of its scenarios
# approved. The summary line added minutes earlier repeated the claim louder.

def _u(sid, status):
    return {"key": "PROJ-1", "scenario_id": sid, "file": "", "gate_status": "",
            "ci_last": "", "plan_status": status}


def test_a_draft_plans_scenarios_are_not_called_approved():
    out = "\n".join(trace_matrix.render_text([_u("S1", "draft")]))
    assert "APPROVED SCENARIO" not in out, \
        "a draft plan's scenario is still labelled approved"
    assert "DRAFT" in out and "not approved" in out


def test_an_approved_plans_scenarios_are_called_approved():
    """The control. Refusing to ever say APPROVED would pass the test above
    while removing the line an audit is actually looking for."""
    out = "\n".join(trace_matrix.render_text([_u("S1", "approved")]))
    assert "APPROVED SCENARIO WITH NO TEST" in out
    assert "not approved" not in out


def test_the_two_kinds_are_counted_separately():
    """Summing them would restore the original lie in aggregate form."""
    rows = [_u("S1", "approved"), _u("S2", "draft"), _u("S3", "draft")]
    out = "\n".join(trace_matrix.render_text(rows))
    assert "1 of 3 row(s): APPROVED SCENARIO WITH NO TEST" in out
    assert "2 of 3 row(s): scenario with no test in a plan that is NOT approved" in out


def test_an_unknown_plan_status_is_not_promoted_to_approved():
    """plan_state can be unreadable; absence must not become a sign-off."""
    out = "\n".join(trace_matrix.render_text([_u("S1", "")]))
    assert "APPROVED SCENARIO" not in out
    assert "UNKNOWN" in out


def test_plan_status_is_exported_for_machine_consumers():
    assert "plan_status" in trace_matrix.FIELDS, \
        "an auditor exporting CSV cannot tell approved rows from draft ones"
