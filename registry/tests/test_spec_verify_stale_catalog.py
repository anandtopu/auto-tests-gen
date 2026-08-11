""""No cataloged tests" was said about a key whose test this platform committed.

`spec_verify` joins on catalog `mapping.feature == key`. On this estate the
join finds nothing for PROJ-301, so `make spec-verify KEY=PROJ-301` exited 1
with "no cataloged tests map to this key" -- while the trace matrix shows
PROJ-301-S1 COMMITTED at c1bbffc in e2e-api-tests-1, generated and pushed by
this platform. The estate catalog has 4 rows and none is that spec.

Two situations with opposite fixes were sharing one sentence:

  * nothing has been generated for this key   -> go generate tests
  * tests exist and are committed, but the estate catalog has no row for them
                                              -> refresh the catalog

Found by driving the command, not by reading: the disagreement only showed up
because `spec-savings` had just reported PROJ-301 as 1/3 covered in the same
session. Two surfaces answering the same question from different sources is
what made it visible.

The exit code stays 1 in both cases -- nothing was verified either way, and
"could not verify" is not success. Only the message changes, because the
message is what an operator acts on.
"""
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine" / "lib"))
import spec_verify


def _rows(*specs):
    return [{"test_repo": r, "file": f, "gate_status": g}
            for r, f, g in specs]


def test_a_committed_but_uncataloged_test_is_reported(monkeypatch):
    """THE DEFECT: the platform knew the file and said nothing existed."""
    monkeypatch.setattr(spec_verify, "_tests_for", lambda key: {})
    import trace_matrix
    monkeypatch.setattr(trace_matrix, "build", lambda key: _rows(
        ("e2e-api-tests-1", "suites/orders/x.spec.js", "committed")))
    assert spec_verify._committed_elsewhere("PROJ-301") == [
        ("e2e-api-tests-1", "suites/orders/x.spec.js")]


def test_a_test_already_in_the_catalog_is_not_reported_as_missing(monkeypatch):
    """It is only interesting when the catalog does NOT have it -- otherwise
    verify() would have run it and this branch is unreachable anyway."""
    monkeypatch.setattr(spec_verify, "_tests_for",
                        lambda key: {"e2e-api-tests-1": ["suites/orders/x.spec.js"]})
    import trace_matrix
    monkeypatch.setattr(trace_matrix, "build", lambda key: _rows(
        ("e2e-api-tests-1", "suites/orders/x.spec.js", "committed")))
    assert spec_verify._committed_elsewhere("PROJ-301") == []


@pytest.mark.parametrize("gate", ["quarantined", "no_changes", "would_commit", ""])
def test_only_a_committed_test_counts(monkeypatch, gate):
    """A quarantined or withheld run did not put a test in the repo, so
    claiming one is committed would send the operator looking for a file that
    is not there -- the mirror of the bug being fixed."""
    monkeypatch.setattr(spec_verify, "_tests_for", lambda key: {})
    import trace_matrix
    monkeypatch.setattr(trace_matrix, "build", lambda key: _rows(
        ("e2e-api-tests-1", "suites/orders/x.spec.js", gate)))
    assert spec_verify._committed_elsewhere("PROJ-301") == []


def test_a_scenario_row_with_no_file_is_not_a_test(monkeypatch):
    """The trace matrix emits a row per scenario, including approved scenarios
    with NO test -- the loudest line on an audit, and not a committed file."""
    monkeypatch.setattr(spec_verify, "_tests_for", lambda key: {})
    import trace_matrix
    monkeypatch.setattr(trace_matrix, "build", lambda key: [
        {"scenario_id": "PROJ-301-S2", "file": "", "test_repo": "",
         "gate_status": ""}])
    assert spec_verify._committed_elsewhere("PROJ-301") == []


def test_a_broken_trace_matrix_degrades_to_the_plain_message(monkeypatch):
    """This only improves an error message. If it can raise, it takes down a
    command the operator ran to diagnose something else."""
    monkeypatch.setattr(spec_verify, "_tests_for", lambda key: {})
    import trace_matrix

    def boom(key):
        raise RuntimeError("matrix unavailable")

    monkeypatch.setattr(trace_matrix, "build", boom)
    assert spec_verify._committed_elsewhere("PROJ-301") == []


def test_a_non_dict_row_is_skipped(monkeypatch):
    """Same shape as the state-store sweep: rows are data on disk, and one bad
    entry must not raise out of an error path."""
    monkeypatch.setattr(spec_verify, "_tests_for", lambda key: {})
    import trace_matrix
    monkeypatch.setattr(trace_matrix, "build",
                        lambda key: ["not-a-row", 7, None])
    assert spec_verify._committed_elsewhere("PROJ-301") == []


def test_the_two_messages_name_different_fixes(capsys, monkeypatch):
    """The whole point: an operator reading either sentence must know which of
    the two situations they are in, and what to do about it."""
    import trace_matrix
    monkeypatch.setattr(spec_verify, "verify", lambda key: {})
    monkeypatch.setattr(spec_verify, "_tests_for", lambda key: {})

    monkeypatch.setattr(trace_matrix, "build", lambda key: _rows(
        ("e2e-api-tests-1", "suites/orders/x.spec.js", "committed")))
    assert spec_verify.main(["PROJ-301"]) == 1
    stale = capsys.readouterr().out
    assert "stale catalog, NOT a missing test" in stale
    assert "make bootstrap" in stale, "the fix is not named"
    assert "suites/orders/x.spec.js" in stale, "the file is not named"

    monkeypatch.setattr(trace_matrix, "build", lambda key: [])
    assert spec_verify.main(["ZZ-NOTHING-1"]) == 1
    empty = capsys.readouterr().out
    assert "nothing has been generated for it yet" in empty
    assert "stale catalog" not in empty, \
        "an empty key was told its catalog is stale"


def test_both_paths_still_exit_nonzero(capsys, monkeypatch):
    """Nothing was verified in either case, and "could not verify" is not
    success -- a CI job reading the exit code must not see a pass."""
    import trace_matrix
    monkeypatch.setattr(spec_verify, "verify", lambda key: {})
    monkeypatch.setattr(spec_verify, "_tests_for", lambda key: {})
    for rows in (_rows(("r", "f.spec.js", "committed")), []):
        monkeypatch.setattr(trace_matrix, "build", lambda key, _r=rows: _r)
        assert spec_verify.main(["K-1"]) != 0
        capsys.readouterr()


# ------------------------------------------- the approvals store is not scratch

def test_verifying_an_unknown_key_creates_no_plan_entry(tmp_path, monkeypatch):
    """`spec-verify` ANNOTATES a plan; it must never invent one.

    The attach was `state.get(key, {"history": []})` followed by
    `state[key] = e`, so any key handed to the command became a plan entry.
    Measured while driving it: `spec-verify KEY=ZZ-NOTHING-1` wrote
    `{"history": [], "verification_run": {...}}` into the estate's plan state
    for a ticket that does not exist -- and `make plans` and the Test plans
    view would list it as real.

    PR-path keys legitimately have no plan, so "no entry" is a normal state,
    not an error to paper over.
    """
    import plan_state
    plans = tmp_path / "plans"
    plans.mkdir()
    monkeypatch.setattr(plan_state, "DIR", plans)
    monkeypatch.setattr(plan_state, "FILE", plans / "state.json")
    # `_tests_for` returns NOTHING, which is the shape the real defect had:
    # ZZ-NOTHING-1 has no cataloged tests and still got an entry. An earlier
    # version of this test returned a repo instead, so verify() went down the
    # clone path, never reached the attach, and PASSED with the guard removed
    # -- a pin that could not see the bug it was written for.
    monkeypatch.setattr(spec_verify, "_tests_for", lambda key: {})
    spec_verify.verify("ZZ-NOTHING-1")
    assert not (plans / "state.json").exists() or \
        "ZZ-NOTHING-1" not in json.loads(
            (plans / "state.json").read_text(encoding="utf-8")), \
        "a key with no plan was given one"


def test_an_existing_plan_still_gets_its_verification_run(tmp_path, monkeypatch):
    """The fix must not disable the feature: a real plan is still annotated."""
    import plan_state
    plans = tmp_path / "plans"
    plans.mkdir()
    (plans / "state.json").write_text(
        json.dumps({"PROJ-9": {"status": "approved", "history": []}}),
        encoding="utf-8")
    monkeypatch.setattr(plan_state, "DIR", plans)
    monkeypatch.setattr(plan_state, "FILE", plans / "state.json")
    monkeypatch.setattr(spec_verify, "_tests_for", lambda key: {})
    spec_verify.verify("PROJ-9")
    entry = json.loads((plans / "state.json").read_text(encoding="utf-8"))["PROJ-9"]
    assert "verification_run" in entry, "an existing plan lost its annotation"
    assert entry["status"] == "approved", "the annotation overwrote plan state"
