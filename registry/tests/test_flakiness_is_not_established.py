""""No flaky tests" was printed for an estate where nothing could be judged.

FOUND BY DRIVING use case 6 exactly as `docs/use-cases.md` writes it: ingest a
JUnit file, then ask which tests are flaky. `qa.py flaky` answered

    no flaky tests detected (needs CI history - POST JUnit results to
    /hooks/ci/results or run: bin/qa.py ingest-results <junit.xml>)

IMMEDIATELY AFTER a successful ingest that reported "2 case(s) matched", with
both tests' history on disk. The empty-store message was serving a populated
store, telling an operator to do the thing they had just done.

THE SIBLING WAS THE WORSE INSTANCE, as it keeps being. `team_report` printed

    - Flaky tests from CI ingest: none

which is an estate-health row a lead reads as "the suite is stable" - and it sat
two lines below that same function's CORRECT treatment of unobservable
coverage ("N repo(s) NOT checked - that count excludes them"). The honest
sibling was in the adjacent line of the same file, for the second time in this
module's history.

THE UNDERLYING RULE WAS ALWAYS RIGHT AND ONLY THE SURFACES WERE WRONG:
`ingest` has always required `runs >= 3` before calling anything flaky, because
one failure out of one run is indistinguishable from a test that is simply
broken. So "no flaky tests" has three meanings and they need three different
actions (C13):

  no_history    nothing ingested        -> wire CI up
  insufficient  history, nothing judged -> keep ingesting; NOTHING is established
  established   judged, none in band    -> the only state that earns "none"
"""
import json
import os
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))

import team_report                                        # noqa: E402
import test_health                                        # noqa: E402


def _entry(runs, failures):
    return {"runs": runs, "failures": failures,
            "pass_rate": round(1 - failures / runs, 3),
            "last_status": "passed",
            "flaky": (0.05 < failures / runs < 0.95
                      and runs >= test_health.MIN_RUNS_TO_JUDGE)}


def test_an_empty_store_and_a_young_one_are_different_answers():
    assert test_health.flakiness_state({})[0] == "no_history"
    young = {"t1": _entry(1, 1), "t2": _entry(2, 0)}
    state, d = test_health.flakiness_state(young)
    assert state == "insufficient", (state, d)
    assert d["tracked"] == 2 and d["judged"] == 0
    # THE POINT: a test that failed its only run is not flaky, and must not be
    # counted as evidence that nothing is.
    assert d["flaky"] == []


def test_a_judged_estate_with_no_flakes_earns_the_plain_answer():
    """THE OVER-FIX DIRECTION. If every state hedged, the marker would mean
    nothing and a real all-clear could never be reported."""
    state, d = test_health.flakiness_state({"t1": _entry(10, 0),
                                            "t2": _entry(10, 10)})
    assert state == "established", (state, d)
    assert d["judged"] == 2


def test_a_real_flake_still_reports_as_one():
    state, d = test_health.flakiness_state({"t1": _entry(4, 1),
                                            "t2": _entry(1, 0)})
    assert state == "flaky" and d["flaky"] == ["t1"], (state, d)


def test_the_judged_count_survives_junk_run_values():
    """`True` is an `int` in Python, so a boolean would be counted as 1 run and
    a string would raise - the same defensive rule record_caveats needed."""
    for junk in (True, "3", None, -1, [3], {"n": 3}):
        state, d = test_health.flakiness_state({"t": {"runs": junk,
                                                      "failures": 0}})
        assert state in ("no_history", "insufficient", "established"), junk
        assert d.get("judged", 0) == 0, (junk, d)


def test_the_minimum_the_surfaces_quote_is_the_one_ingest_enforces():
    """A surface naming a different number sends the operator to wait for runs
    that would still not produce a verdict."""
    src = (ROOT / "engine/lib/test_health.py").read_text(encoding="utf-8")
    assert "h[\"runs\"] >= MIN_RUNS_TO_JUDGE" in src, \
        "ingest no longer uses the constant the surfaces quote, so the two " \
        "can drift"


@pytest.mark.parametrize("counts,expect,bare_none", [
    ({"flaky": [], "flaky_state": "no_history", "flaky_detail": {}},
     "NOT KNOWN", False),
    ({"flaky": [], "flaky_state": "insufficient",
      "flaky_detail": {"tracked": 2, "need": 3}}, "NOT KNOWN", False),
    ({"flaky": [], "flaky_state": "established",
      "flaky_detail": {"judged": 5}}, "none", True),
])
def test_the_estate_health_row_says_which_none_it_means(counts, expect,
                                                        bare_none):
    """Driven against a FABRICATED counts dict, so the rule is checkable
    independently of whatever this estate's health file happens to hold -- the
    precedent `_repair_loop_cell` set in this same file.

    THE DEFECT IS THE LINE *LEADING* WITH "none", not the word appearing at
    all: the honest insufficient message says "...but NONE has the 3 runs
    needed", and a blanket substring check flagged it. A pin that cries wolf on
    correct code is one somebody deletes.
    """
    line = team_report._flaky_line(counts)
    assert expect in line, line
    assert line.startswith("none") is bare_none, line
    if bare_none:
        # Even the all-clear must say what it was judged against: a naked
        # "none" is the rendering being replaced, and it is one edit away
        # from coming back.
        assert "enough runs to judge" in line, line


def test_the_row_still_names_the_flaky_tests():
    line = team_report._flaky_line({"flaky": ["a::b::c"],
                                    "flaky_state": "flaky"})
    assert "a::b::c" in line and "NOT KNOWN" not in line, line


def _flaky_cli(health_file):
    r = subprocess.run([sys.executable, str(ROOT / "bin/qa.py"), "flaky"],
                       cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace",
                       stdin=subprocess.DEVNULL,
                       env={**os.environ,
                            "AIQE_HEALTH_FILE": str(health_file)})
    assert r.returncode == 0, r.stderr[-500:]
    return r.stdout


def test_the_cli_does_not_tell_you_to_do_what_you_just_did(tmp_path):
    """DRIVEN. This is the exact state the defect was measured in: a successful
    ingest, two tests tracked, one run each."""
    f = tmp_path / "health.json"
    f.write_text(json.dumps({"t1": _entry(1, 0), "t2": _entry(1, 1)}),
                 encoding="utf-8")
    out = _flaky_cli(f)
    assert "not enough runs to tell" in out, out
    assert "ingest-results <junit.xml>" not in out, \
        "the CLI still offers the wire-CI-up fix to an operator whose CI is " \
        "already wired up"


def test_the_cli_still_offers_the_setup_fix_when_there_is_no_history(tmp_path):
    """The other direction: on a genuinely empty store that advice is right,
    and losing it would leave a newcomer with nothing to do."""
    f = tmp_path / "health.json"
    f.write_text("{}", encoding="utf-8")
    out = _flaky_cli(f)
    assert "ingest-results <junit.xml>" in out, out
    assert "not enough runs" not in out, out


def test_the_cli_reports_a_judged_all_clear_plainly(tmp_path):
    f = tmp_path / "health.json"
    f.write_text(json.dumps({"t1": _entry(10, 0)}), encoding="utf-8")
    out = _flaky_cli(f)
    assert "no flaky tests among the 1 test(s)" in out, out
    assert "NOT" not in out and "not enough" not in out, out


def test_every_surface_reporting_flakiness_asks_the_one_decision_function():
    """THE INVARIANT, not today's two call sites. A third surface deriving
    `if h.get("flaky")` itself is how there came to be two that disagreed."""
    for rel in ("bin/qa.py", "engine/lib/team_report.py"):
        src = (ROOT / rel).read_text(encoding="utf-8")
        assert "flakiness_state" in src, \
            f"{rel} reports on flakiness without asking what can be established"
