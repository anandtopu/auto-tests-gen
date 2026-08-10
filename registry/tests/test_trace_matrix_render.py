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


# --- the same claim on the savings surface ----------------------------------

def test_spec_savings_exposes_the_plan_status_it_is_reporting_on():
    """Follow-up to the trace-matrix fix: spec_savings counts scenarios from
    the spec file with NO approval check, while the dashboard described them
    as "approved scenario(s) already covered". The counts are legitimately
    useful for a DRAFT plan, so filtering to approved-only would break the
    common case -- what has to go is the unearned word, which means the
    surface needs the real status."""
    import spec_savings
    plan = spec_savings.authoring_plan("PROJ-301")
    assert "plan_status" in plan, \
        "callers cannot tell whether these scenarios were ever signed off"


def test_an_unreadable_plan_status_is_empty_not_approved():
    """Absence must not become the flattering answer."""
    import spec_savings
    assert spec_savings._plan_status("NO-SUCH-KEY-ZZ") == ""


def test_the_dashboard_no_longer_calls_them_approved():
    src = (ROOT / "bin/dashboard.py").read_text(encoding="utf-8")
    assert "approved scenario(s) already covered" not in src, \
        "the savings card still asserts a sign-off it has not checked"
    assert "plan_status" in src, "the card does not show the real status"


# --- waivers: a deliberate absence is not an unexplained gap ----------------

def _wrow(**kw):
    r = {"key": "K-1", "scenario_id": "K-1-S1", "file": "", "gate_status": "",
         "ci_last": "", "plan_status": "approved", "waiver": ""}
    r.update(kw)
    return r


def test_a_validly_waived_scenario_is_not_counted_as_a_gap():
    """The gate accepts an approved scenario that is covered OR carries a
    non-expired waiver (engine/gate/spec_check.py). This report computed the
    waiver per row and never mentioned it, so a scenario somebody had
    explicitly waived was still counted in the loudest line an audit reads --
    a report contradicting the component that decides whether code ships."""
    lines = trace_matrix.render_text([_wrow(waiver="waived: accepted risk (qa-lead)")])
    body = "\n".join(lines)
    assert "APPROVED SCENARIO WITH NO TEST" not in body, \
        "a validly waived scenario is still reported as an unexplained gap"
    assert "WAIVED" in body, "the waiver is not surfaced at all"
    assert "the gate accepts" in body, \
        "the reader is not told why this one is different"


def test_an_expired_waiver_is_still_a_gap():
    """The case most worth surfacing, not least: somebody decided this was
    temporary and the clock ran out. Treating it as waived would hide exactly
    the decision that has lapsed."""
    lines = trace_matrix.render_text([_wrow(waiver="waived (EXPIRED): old reason (qa-lead)")])
    body = "\n".join(lines)
    assert "APPROVED SCENARIO WITH NO TEST" in body, \
        "an EXPIRED waiver was treated as still in force"


def test_waived_and_unwaived_are_counted_separately():
    """Both numbers matter: how many gaps, and how many waivers are in force."""
    lines = trace_matrix.render_text([
        _wrow(scenario_id="K-1-S1", waiver="waived: risk accepted (lead)"),
        _wrow(scenario_id="K-1-S2"),
    ])
    body = "\n".join(lines)
    assert "1 of 2 row(s): approved scenario with no test, WAIVED" in body
    assert "1 of 2 row(s): APPROVED SCENARIO WITH NO TEST" in body
    assert "K-1-S1" in body and "K-1-S2" in body


def test_a_draft_scenario_with_a_waiver_is_still_reported_as_draft():
    """Waiver handling must not quietly promote an unapproved plan: a waiver on
    a draft scenario says nothing about a sign-off that never happened."""
    lines = trace_matrix.render_text([_wrow(plan_status="draft",
                                 waiver="waived: risk accepted (lead)")])
    body = "\n".join(lines)
    assert "NOT approved" in body, \
        "a waiver made a draft scenario stop being reported as draft"
    # ...and it must not ALSO appear on the waived line. Dropping the
    # _approved() guard puts the row in BOTH lists, and asserting only on the
    # draft line let that mutation survive: the row was double-counted and the
    # per-line totals stopped summing to the number of rows.
    assert "WAIVED (not a gap" not in body, \
        "a draft scenario was counted as a waived approved scenario as well"


def test_the_dashboard_trace_table_agrees_with_the_gate_about_waivers():
    """The sibling. The UI outlined EVERY row without a test as a warning and
    showed a 'no test yet' chip, waiver or not — so the table disagreed with
    the gate, which accepts an approved scenario carrying a non-expired waiver.
    Milder than the CLI's 'APPROVED SCENARIO WITH NO TEST', same family."""
    src = (ROOT / "bin/dashboard.py").read_text(encoding="utf-8")
    fn = src[src.index("async function refreshTraceMatrix("):]
    fn = fn[:fn.index("refreshTraceMatrix();")]
    assert "r.waiver" in fn, \
        "the trace table ignores the waiver column its own API returns"
    # Assert the EXPRESSION, not the word. The first version checked for
    # "EXPIRED" anywhere in the function, and a mutation deleting the check
    # survived it -- because the word still appeared in the comment I had
    # written directly above the code. The pin was matching my own prose.
    assert "r.waiver.indexOf('EXPIRED') < 0" in fn, \
        "the UI no longer excludes expired waivers from the valid-waiver test"
    # The warning outline must be gated on the waiver, not on file alone.
    assert "const noTest = !r.file && !waived" in fn, \
        "a validly waived row is still outlined as a warning"
