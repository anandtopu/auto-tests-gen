""""We were told not to push" is not "there was nothing to push".

`gate.sh` emits `GATE_STATUS=WOULD_COMMIT` and exits 0 in check-only mode
(AIQE_GATE_CHECK_ONLY). pipeline.sh classified "exit 0 and not COMMITTED" as
`no_changes`, so a run whose generated tests passed EVERY gate check and were
deliberately withheld was recorded as having produced nothing.

Reproduced end to end before the fix, with a full mock PR run:

    overall = no_changes
    gates   = [(e2e-api-tests-1, no_changes), (e2e-ui-tests-1, no_changes)]

The two readings lead to opposite actions. "No changes" tells a reviewer the
run found nothing worth shipping. "Would commit" tells them work is ready and
blocked on a flag. pipeline.sh never SETS the flag, but it never clears it
either and `.env` is loaded, so an operator who left it set gets this on every
run — silently, because the gate exits 0 either way.
"""
import json
import os
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine" / "lib"))
sys.path.insert(0, str(ROOT / "eval"))


def _scorecard():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_sc_helpers", ROOT / "eval/scorecard.py")
    mod = importlib.util.module_from_spec(spec)
    # scorecard.py runs its report at import. Only the pure helper is wanted,
    # so compile just that function rather than executing the module.
    src = (ROOT / "eval/scorecard.py").read_text(encoding="utf-8")
    start = src.index("def commit_rate_line")
    end = src.index("\n# --- routing accuracy", start)
    ns = {"pct": lambda x: f"{x:.0%}"}
    exec(compile(src[start:end], "scorecard_helper", "exec"), ns)
    return ns["commit_rate_line"]


def _run(overall):
    return {"overall": overall}


def test_a_withheld_run_is_excluded_from_the_commit_rate():
    line = _scorecard()([_run("committed"), _run("committed"),
                         _run("would_commit")])
    assert "100% of 2 runs" in line, \
        f"a check-only run was counted as a failure to commit: {line}"


def test_the_exclusion_is_named_not_silent():
    """A denominator that shrinks with no explanation is its own lie."""
    line = _scorecard()([_run("committed"), _run("would_commit")])
    assert "1 excluded" in line
    assert "AIQE_GATE_CHECK_ONLY" in line, \
        "the reader is not told WHY the denominator is smaller"


def test_no_withheld_runs_adds_no_clause():
    line = _scorecard()([_run("committed"), _run("quarantined")])
    assert "excluded" not in line
    assert "50% of 2 runs" in line


def test_an_all_withheld_estate_reports_n_a_rather_than_zero():
    """0/0 must not render as 0%: "the gate committed nothing" would read as a
    total failure when the gate was told to commit nothing."""
    line = _scorecard()([_run("would_commit"), _run("would_commit")])
    assert line.startswith("Commit rate: n/a")
    assert "AIQE_GATE_CHECK_ONLY" in line
    assert "0%" not in line


def test_the_pipeline_classifies_would_commit_before_the_generic_zero_exit():
    """Order is the whole defect: the generic `exit 0` branch swallowed it. The
    WOULD_COMMIT test must come FIRST."""
    src = (ROOT / "engine/pipeline.sh").read_text(encoding="utf-8")
    wc = src.index('GATE_STATUS=WOULD_COMMIT"; then')
    generic = src.index('elif [ $GRC -eq 0 ]; then')
    assert wc < generic, "the generic exit-0 branch still swallows WOULD_COMMIT"
    assert "ST=would_commit" in src


def test_the_record_ranks_withheld_above_no_changes_and_below_committed():
    src = (ROOT / "engine/lib/run_record.py").read_text(encoding="utf-8")
    committed = src.index('"committed" if any(g["status"] == "committed"')
    withheld = src.index('"would_commit" if any(g["status"] == "would_commit"')
    nochanges = src.index('else "no_changes")')
    assert committed < withheld < nochanges, (
        "a run with one repo committed and another withheld must still be "
        "`committed`; a run with neither must not be `no_changes`")


def test_a_check_only_run_records_would_commit_end_to_end(tmp_path):
    """The only pin that proves the whole chain -- gate emits, pipeline
    classifies, record ranks. Everything above reads source text, and this
    session has repeatedly shown that source-text pins miss what driving finds.

    ~50s: it runs the real pipeline in mock mode. Deliberately NOT marked
    `slow` -- this repo registers no such marker, nothing filters on it,
    and an unregistered mark emits a warning into an otherwise clean run. It cleans up its own run
    record, because a test that leaves records behind feeds the scorecard its
    own traffic.
    """
    import work_queue
    runs = ROOT / "reports/runs"
    before = {p.name for p in runs.glob("[0-9]*.json")}
    r = subprocess.run(
        [work_queue.bash_exe(), "engine/pipeline.sh", "pr", "orders-api", "201"],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        cwd=str(ROOT), stdin=subprocess.DEVNULL, timeout=900,
        env={**os.environ, "AIQE_MOCK": "1", "AIQE_GATE_CHECK_ONLY": "1"})
    created = {p.name for p in runs.glob("[0-9]*.json")} - before
    try:
        assert r.returncode == 0, r.stdout[-800:]
        assert "would commit" in r.stdout, \
            "the summary does not say the work was withheld"
        assert "AIQE_GATE_CHECK_ONLY" in r.stdout, \
            "the summary does not name the flag that withheld it"
        assert created, "the run wrote no record"
        # sorted(...)[0], NOT created.pop(): pop MUTATES the set, so the
        # `finally` below iterated an empty collection and cleaned up NOTHING.
        # Caught by the scorecard line this same commit added -- a full review
        # reported "8 excluded: the gate ran in check-only mode", one record
        # per mutation pass, in the test whose docstring promises it leaves
        # none. The tidy-up has to be independent of what the assertions read.
        rec = json.loads((runs / sorted(created)[0]).read_text(encoding="utf-8"))
        assert rec["overall"] == "would_commit", \
            f"a withheld run was recorded as {rec['overall']!r}"
        assert any(g["status"] == "would_commit" for g in rec["gates"])
    finally:
        for name in created:
            (runs / name).unlink(missing_ok=True)
            for extra in runs.glob(name.replace(".json", "") + "*.diff"):
                extra.unlink(missing_ok=True)
