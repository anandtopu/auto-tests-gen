"""Traceability matrix (roadmap 3.1) — the audit join.

Pins: scenario→test joins on the stamped scenario_id; an APPROVED scenario with no
test still gets a row (the most important row on an audit); PR-path tests with no
plan appear rather than being hidden; CI health joins by test_id with a by-file
fallback; corrupt inputs degrade to empty cells, never errors.
"""
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
import trace_matrix as tm


@pytest.fixture
def estate(tmp_path, monkeypatch):
    """A miniature estate: one JIRA run with a plan, one PR run without."""
    import plan_state
    import test_health
    runs = tmp_path / "reports/runs"
    runs.mkdir(parents=True)
    monkeypatch.setattr(tm, "ROOT", tmp_path)
    monkeypatch.setattr(plan_state, "DIR", tmp_path / "reports/plans")
    monkeypatch.setattr(plan_state, "FILE", tmp_path / "reports/plans/state.json")
    monkeypatch.setattr(test_health, "FILE", tmp_path / "health.json")

    (tmp_path / "reports/plans").mkdir(parents=True)
    (tmp_path / "reports/plans/PROJ-9.contract.json").write_text(json.dumps({
        "scenarios": [
            {"id": "PROJ-9-S1", "title": "boundary rejection", "behavior_ref": "B2"},
            {"id": "PROJ-9-S2", "title": "authz rejection", "behavior_ref": "B3"},
        ]}), encoding="utf-8")

    (runs / "100-1.json").write_text(json.dumps({
        "run_id": "100-1", "ts": 100, "trigger": {"type": "tests", "key": "PROJ-9"},
        "gates": [{"test_repo": "e2e-api", "status": "committed", "commit": "abc1234def"}],
        "phases": [{"name": "generate", "contract": {"tests": [
            {"file": "suites/a.spec.js", "name": "PROJ-9: boundary",
             "scenario_id": "PROJ-9-S1", "action": "created", "repo": "e2e-api"}]}}],
    }), encoding="utf-8")
    (runs / "200-2.json").write_text(json.dumps({
        "run_id": "200-2", "ts": 200, "trigger": {"type": "pr", "key": "PR-svc-7"},
        "gates": [{"test_repo": "e2e-api", "status": "committed", "commit": "fff000111"}],
        "phases": [{"name": "generate", "contract": {"tests": [
            {"file": "suites/pr.spec.js", "name": "PR-svc-7: x",
             "scenario_id": "PR-svc-7-S1", "action": "updated", "repo": "e2e-api"}]}}],
    }), encoding="utf-8")
    (runs / "torn.json").write_text('{"run_id": "torn', encoding="utf-8")

    (tmp_path / "health.json").write_text(json.dumps({
        "e2e-api::suites/a.spec.js::PROJ-9: boundary":
            {"runs": 12, "failures": 1, "last_status": "passed"}}), encoding="utf-8")
    return tmp_path


def test_scenario_joins_to_its_test_and_health(estate):
    rows = {(r["key"], r["scenario_id"]): r for r in tm.build("PROJ-9")}
    r = rows[("PROJ-9", "PROJ-9-S1")]
    assert r["file"] == "suites/a.spec.js"
    assert r["gate_status"] == "committed" and r["commit"] == "abc1234de"
    assert r["ci_runs"] == 12 and r["ci_last"] == "passed"


def test_single_gate_supplies_repo_for_legacy_generate_contract(estate):
    """Single-agent contracts predate per-test ``repo`` metadata.

    Their one gate is unambiguous. Dropping that link makes the audit row show
    a generated spec beside blank repo/gate/commit cells even though the same
    run records a successful commit.
    """
    record_path = estate / "reports/runs/100-1.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    del record["phases"][0]["contract"]["tests"][0]["repo"]
    record_path.write_text(json.dumps(record), encoding="utf-8")

    row = next(r for r in tm.build("PROJ-9")
               if r["scenario_id"] == "PROJ-9-S1")
    assert row["test_repo"] == "e2e-api"
    assert row["gate_status"] == "committed"
    assert row["commit"] == "abc1234de"


def test_multi_gate_legacy_contract_does_not_guess_a_repo(estate):
    record_path = estate / "reports/runs/100-1.json"
    record = json.loads(record_path.read_text(encoding="utf-8"))
    del record["phases"][0]["contract"]["tests"][0]["repo"]
    record["gates"].append({
        "test_repo": "e2e-ui", "status": "committed", "commit": "def5678",
    })
    record_path.write_text(json.dumps(record), encoding="utf-8")

    row = next(r for r in tm.build("PROJ-9")
               if r["scenario_id"] == "PROJ-9-S1")
    assert row["test_repo"] == "", "an owner was guessed from several gates"
    # The gate cell used to be "" here too -- the same value the test below
    # asserts for a scenario with NO test at all. This file was pinning the
    # conflation: a committed spec whose owner we cannot establish read exactly
    # like a requirement nothing exercises. The no-guess INTENT above is what
    # this test is for, and it is unchanged; the empty string was only its
    # incidental side effect.
    assert row["gate_status"] == "unattributed"
    uncovered = next(r for r in tm.build("PROJ-9")
                     if r["scenario_id"] == "PROJ-9-S2")
    assert row["gate_status"] != uncovered["gate_status"], \
        "a test that exists is indistinguishable from one that does not"


def test_approved_scenario_with_no_test_still_gets_a_row(estate):
    rows = {(r["key"], r["scenario_id"]): r for r in tm.build("PROJ-9")}
    orphan = rows[("PROJ-9", "PROJ-9-S2")]
    assert orphan["file"] == "" and orphan["gate_status"] == "", \
        "a requirement nothing exercises must be visible, not dropped"


def test_pr_path_tests_without_a_plan_are_not_hidden(estate):
    rows = [r for r in tm.build() if r["key"] == "PR-svc-7"]
    assert len(rows) == 1 and rows[0]["file"] == "suites/pr.spec.js"
    assert rows[0]["scenario_id"] == "", "no plan means no scenario claim"


def test_matrix_is_total_over_torn_records(estate):
    rows = tm.build()          # torn.json present in the store
    assert {r["key"] for r in rows} == {"PROJ-9", "PR-svc-7"}


def test_csv_has_the_declared_columns(estate):
    csv_text = tm.to_csv(tm.build())
    header = csv_text.splitlines()[0].split(",")
    assert header == tm.FIELDS
    assert "PROJ-9-S2" in csv_text, "the no-test row survives export"
