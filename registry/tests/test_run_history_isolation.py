"""The estate's run history must not record the test suite's own failures.

`reports/runs/*.json` is operational data: `make status` lists it, the cost
report attributes spend to it, and `eval/scorecard.py` computes the platform's
headline quality number from it.

The fan-out containment test registers a deliberately unclonable repo to prove
a per-repo failure is contained. The pipeline correctly records
`overall: quarantined` — and that record landed in the shared history. In a
measured estate EVERY quarantined run was one of these, and the scorecard read:

    Commit rate: 81% of 16 runs (3 quarantined)

after cleanup:

    Commit rate: 100% of 37 runs (0 quarantined)

So the platform's own quality metric was reporting its test scaffolding as
product failure, understating the real commit rate by 19 points. Nobody would
notice: a quarantined run is a legitimate outcome, and the number moves in the
direction that looks like honest self-criticism.

The test now removes the record it caused, and conftest sweeps any left behind
by a killed run. These pin the sweep, because a sweep that silently stops
selecting is indistinguishable from a clean estate.
"""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "registry/tests"))
import conftest  # noqa: E402


def _rec(gates):
    return {"run_id": "1-1", "trigger": {"type": "pr", "key": "PR-x-1"},
            "overall": "quarantined",
            "gates": [{"test_repo": r, "status": s} for r, s in gates]}


def test_a_record_naming_a_fixture_repo_is_selected(tmp_path):
    (tmp_path / "a.json").write_text(
        json.dumps(_rec([("zz-nofetch", "clone_failed"),
                         ("e2e-api-tests-1", "committed")])), encoding="utf-8")
    got = [p.name for p in conftest.fixture_tainted_runs(tmp_path)]
    assert got == ["a.json"]


def test_a_real_run_is_never_touched(tmp_path):
    """Selection is by CONTENT — a gate naming a fixture repo — never by age,
    by outcome, or by 'looks synthetic'. A genuinely quarantined production run
    is exactly the record an operator most needs to keep."""
    (tmp_path / "real.json").write_text(
        json.dumps(_rec([("e2e-api-tests-1", "clone_failed"),
                         ("e2e-ui-tests-1", "committed")])), encoding="utf-8")
    assert conftest.fixture_tainted_runs(tmp_path) == []


def test_the_shared_state_files_are_never_selected(tmp_path):
    """reviews.json, queue.json and hooks-seen.json live in the same directory
    and are NOT run records. CLAUDE.md calls this out because every glob over
    reports/runs has to skip them; deleting reviews.json would destroy the
    team's review decisions.

    They are excluded BY NAME, not by shape — so the fixtures here deliberately
    carry the exact structure a tainted run record has. Written the obvious way
    (a plain reviews.json map with no `gates` key) the assertion passed even
    with the name check removed, which proved nothing.
    """
    for name in ("reviews.json", "queue.json", "hooks-seen.json"):
        (tmp_path / name).write_text(
            json.dumps(_rec([("zz-nofetch", "clone_failed")])), encoding="utf-8")
    assert conftest.fixture_tainted_runs(tmp_path) == [], \
        "a shared state file was selected for deletion — reviews.json holds " \
        "the team's review decisions"


def test_unreadable_and_malformed_records_are_skipped_not_deleted(tmp_path):
    """A record we cannot parse is not evidence that it is ours. Treating it as
    tainted would let a corrupt file be deleted rather than investigated."""
    (tmp_path / "torn.json").write_text('{"gates": [{"test_repo"', encoding="utf-8")
    (tmp_path / "empty.json").write_text("", encoding="utf-8")
    assert conftest.fixture_tainted_runs(tmp_path) == []


def test_a_record_with_no_gates_block_is_not_tainted(tmp_path):
    (tmp_path / "b.json").write_text(json.dumps({"run_id": "9"}), encoding="utf-8")
    (tmp_path / "c.json").write_text(json.dumps({"gates": None}), encoding="utf-8")
    assert conftest.fixture_tainted_runs(tmp_path) == []


def test_a_json_file_that_is_not_an_object_does_not_crash_the_sweep(tmp_path):
    """`queue.json` is a LIST. This runs in pytest_sessionstart, so `rec.get`
    on a list did not fail the sweep — it killed the whole suite with an
    INTERNALERROR whose traceback pointed here rather than at the file that
    caused it. Found by mutating the name-skip away."""
    (tmp_path / "listy.json").write_text(json.dumps([{"gates": []}]), encoding="utf-8")
    (tmp_path / "stringy.json").write_text(json.dumps("zz-nofetch"), encoding="utf-8")
    (tmp_path / "nully.json").write_text("null", encoding="utf-8")
    assert conftest.fixture_tainted_runs(tmp_path) == []


def test_the_estate_history_is_currently_clean():
    """The end state, asserted against the real directory. Runs before
    test_ui_features re-creates one (alphabetical order), so it reflects the
    session-start sweep."""
    tainted = conftest.fixture_tainted_runs()
    assert not tainted, (
        "fixture-produced run records in the estate's history — the scorecard "
        "counts them:\n  " + "\n  ".join(p.name for p in tainted))


def test_the_fanout_test_cleans_up_the_record_it_causes():
    """Source pin, and deliberately so: the conftest sweep runs at SESSION
    START, so a behavioural assertion inside the same session cannot observe
    the fan-out test failing to clean up after itself. Without this, the sweep
    silently becomes the only thing holding the line."""
    src = (ROOT / "registry/tests/test_ui_features.py").read_text(encoding="utf-8")
    body = src[src.index("def test_clone_failure_skips_the_repo_but_commits_the_rest"):]
    body = body[:body.index("\ndef ")]
    assert "reports/runs" in body and "unlink()" in body, \
        "the fan-out test no longer removes the run record it creates"
