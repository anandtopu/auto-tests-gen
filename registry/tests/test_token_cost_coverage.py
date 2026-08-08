"""TCA-A2 evaluator contract and adversarial accounting assertions."""
import importlib.util
import pathlib
import subprocess

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "token_cost_coverage", ROOT / "eval/token_cost_coverage.py")
tca = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
SPEC.loader.exec_module(tca)


def _result(status=0):
    return subprocess.CompletedProcess(["pipeline"], status, "", "")


def _doc(mode="jira", phase="analyze", basis="simulated"):
    return {"mode": mode, "run_id": "1-1", "rows": [{
        "phase": phase, "basis": basis, "attribution": "eval"}]}


def test_eval_is_wired_and_enumerates_all_modes_and_abort_paths():
    makefile = (ROOT / "Makefile").read_text(encoding="utf-8")
    assert "python3 eval/token_cost_coverage.py" in makefile
    assert {row[1] for row in tca.SCENARIOS} == {
        "pr", "jira", "plan", "tests", "requirements"}
    assert {row[3] for row in tca.SCENARIOS} >= {0, 65, 77, 143}


def test_budget_abort_rejects_a_false_row_for_the_never_started_phase():
    doc = _doc()
    doc["rows"].append({"phase": "testplan", "basis": "unrecorded",
                        "attribution": "eval"})
    with pytest.raises(tca.CoverageFailure, match="never-started"):
        tca.validate_entry("budget_abort_77", "jira", 77,
                           _result(77), doc, False)


def test_mid_phase_kill_requires_an_explicit_unrecorded_row():
    with pytest.raises(tca.CoverageFailure, match="unrecorded analyze"):
        tca.validate_entry("mid_phase_kill", "jira", 143,
                           _result(143), _doc(basis="simulated"), False)
    observed = tca.validate_entry("mid_phase_kill", "jira", 143,
                                  _result(143), _doc(basis="unrecorded"), False)
    assert observed["durable_entry"] and observed["lock_released"]


def test_any_stale_lock_or_wrong_attribution_fails_the_metric():
    with pytest.raises(tca.CoverageFailure, match="pipeline.lock"):
        tca.validate_entry("jira", "jira", 0, _result(), _doc(), True)
    doc = _doc()
    doc["rows"][0]["attribution"] = "user"
    with pytest.raises(tca.CoverageFailure, match="eval attribution"):
        tca.validate_entry("jira", "jira", 0, _result(), doc, False)


def test_mock_controls_are_provider_local_and_after_the_start_marker():
    mock = (ROOT / "engine/phases/mock_phase.sh").read_text(encoding="utf-8")
    marker = "spend_ledger.py mark-start"
    kill = 'kill -TERM "$$"'
    assert marker in mock and kill in mock and mock.index(marker) < mock.index(kill)
    assert "AIQE_MOCK_BLOCKING_CLARIFICATION" in mock
    real = (ROOT / "engine/phases/run_phase.sh").read_text(encoding="utf-8")
    assert "AIQE_MOCK_KILL_PHASE" not in real
