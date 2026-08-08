"""A3: plan-first from a PR reuses the signed plan lifecycle end to end."""
import importlib.util
import json
import os
import pathlib
import subprocess

import plan_state
import pytest
import work_queue


ROOT = pathlib.Path(__file__).resolve().parents[2]
BASH = work_queue.bash_exe()
KEY = "PR-orders-api-201"
_SPEC = importlib.util.spec_from_file_location(
    "a3_spec_check", ROOT / "engine/gate/spec_check.py")
spec_check = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(spec_check)


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    plans = tmp_path / "plans"
    monkeypatch.setattr(plan_state, "DIR", plans)
    monkeypatch.setattr(plan_state, "FILE", plans / "state.json")
    monkeypatch.setattr(plan_state, "PLAN_DIR", tmp_path / "testplans")
    monkeypatch.setattr(work_queue, "FILE", tmp_path / "queue.json")
    monkeypatch.setenv("AIQE_PR_PLAN", "1")
    return tmp_path


def test_pr_plan_queue_is_flagged_keyed_and_deduplicated(isolated, monkeypatch):
    item, fresh = work_queue.add("plan", "orders-api", "201", ticket="PROJ-301")
    duplicate, again = work_queue.add("plan", "orders-api", "201", ticket="PROJ-301")
    assert fresh and not again and duplicate["id"] == item["id"]
    assert work_queue.key_of(item) == KEY
    monkeypatch.setenv("AIQE_PR_PLAN", "0")
    with pytest.raises(SystemExit, match="disabled"):
        work_queue.add("plan", "orders-api", "202")


def test_pr_target_lives_on_the_existing_plan_entry_and_edit_revokes(isolated):
    plan_state.PLAN_DIR.mkdir(parents=True)
    plan_state.plan_path(KEY).write_text("# plan\n", encoding="utf-8")
    target = {"kind": "pr", "repo": "orders-api", "pr": "201",
              "ticket": "PROJ-301"}
    entry = plan_state.record_plan(KEY, {"scenarios": []}, target=target)
    assert entry["status"] == "draft" and entry["target"] == target
    plan_state.set_status(KEY, "approved", "qe-lead")
    plan_state.save_plan(KEY, "# changed plan\n", "editor")
    assert plan_state.get(KEY)["status"] == "draft"
    with pytest.raises(SystemExit, match="not approved"):
        plan_state.require_approved(KEY)


def test_pr_plan_requirements_exemption_is_explicit_and_jira_still_refuses(
        isolated, monkeypatch):
    monkeypatch.setattr(plan_state, "_requirements_gate_on", lambda: True)
    decision = plan_state.require_requirements(KEY, pr_target=True)
    assert decision == {"exempt": True,
                        "reason": "PR-keyed plans use diff + fused ticket context"}
    with pytest.raises(SystemExit, match="requirements gate is ON"):
        plan_state.require_requirements("PROJ-301")


def test_signed_pr_spec_is_enforced_and_unsigned_pr_is_exempt(
        isolated, tmp_path, monkeypatch):
    spec_root = tmp_path / "spec-root"
    (spec_root / "out").mkdir(parents=True)
    monkeypatch.setattr(spec_check, "ROOT", spec_root)
    import spec_store
    monkeypatch.setattr(spec_store, "SPEC_DIR", tmp_path / "specs")
    plan_state.PLAN_DIR.mkdir(parents=True, exist_ok=True)
    plan_state.plan_path(KEY).write_text("# plan\n", encoding="utf-8")
    contract = {"scenarios": [{"id": f"{KEY}-S1", "title": "boundary",
                                "layer": "api", "target_repo": "e2e-api-tests-1",
                                "steps": {"given": "x", "when": "y", "then": "z"},
                                "verification": ["status is 422"]}]}
    plan_state.record_plan(KEY, contract,
                           target={"kind": "pr", "repo": "orders-api", "pr": "201"})
    # Pin the gate decision directly; record_plan's best-effort spec write is
    # independently covered by spec_store tests and may be disabled by suite env.
    import yaml
    spec_store.spec_path(KEY).parent.mkdir(parents=True, exist_ok=True)
    spec_store.spec_path(KEY).write_text(
        yaml.safe_dump({"key": KEY, **contract, "open_questions": []}),
        encoding="utf-8")
    plan_state.set_status(KEY, "approved", "qe-lead")
    (spec_root / "out/generate.contract.json").write_text(
        json.dumps({"tests": []}), encoding="utf-8")
    findings, exempt = spec_check.check(KEY, "e2e-api-tests-1", [])
    assert not exempt and any("UNCOVERED_SCENARIO" in f for f in findings)
    findings, exempt = spec_check.check("PR-orders-api-999", "e2e-api-tests-1", [])
    assert exempt and findings == []


def test_mock_pr_plan_comments_both_surfaces_and_writes_no_run_record(tmp_path):
    before = {p.name for p in (ROOT / "reports/runs").glob("*.json")}
    log = ROOT / "out/mock-comments.log"
    old_log = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
    env = {**os.environ, "AIQE_MOCK": "1", "AIQE_PR_PLAN": "1",
           "AIQE_PR_TICKET_CONTEXT": "1",
           "AIQE_PLAN_DIR": str(tmp_path / "plans"),
           "AIQE_TESTPLAN_DIR": str(tmp_path / "testplans"),
           "AIQE_SPEC_DIR": str(tmp_path / "specs"),
           "AIQE_TESTDATA_DIR": str(tmp_path / "testdata")}
    run = subprocess.run([BASH, "engine/pipeline.sh", "plan", "orders-api", "201"],
                         cwd=ROOT, env=env, capture_output=True, text=True,
                         encoding="utf-8", errors="replace", timeout=600,
                         stdin=subprocess.DEVNULL)
    assert run.returncode == 0, run.stdout[-1000:] + run.stderr[-1000:]
    state = json.loads((tmp_path / "plans/state.json").read_text(encoding="utf-8"))
    assert state[KEY]["status"] == "draft"
    assert state[KEY]["target"]["ticket"] == "PROJ-301"
    assert state[KEY]["comments"][-1]["kind"] == "plan"
    assert state[KEY]["comments"][-1]["target"] == "PROJ-301"
    assert state[KEY]["comments"][-1]["outcome"] == "posted"
    assert state[KEY]["comments"][-1]["comment_id"].startswith("mock-")
    added = log.read_text(encoding="utf-8", errors="replace")[len(old_log):]
    assert "comment on orders-api#201" in added
    assert "PROJ-301 <-" in added
    after = {p.name for p in (ROOT / "reports/runs").glob("*.json")}
    assert after == before, "plan-only PR mode must not create a run record"


def test_pr_plan_wizard_and_governance_decisions_are_pinned():
    page = (ROOT / "bin/dashboard.py").read_text(encoding="utf-8")
    pipeline = (ROOT / "engine/pipeline.sh").read_text(encoding="utf-8")
    check = (ROOT / "engine/gate/spec_check.py").read_text(encoding="utf-8")
    assert all(marker in page for marker in
               ("wz-start-pr-plan", "wz-pr-approve", "wz-pr-generate", "pr-plan"))
    assert "(i.mode === 'plan' && i.pr)" in page
    assert 'require-requirements "$KEY" --pr' in pipeline
    assert 'out/pr.diff out/changed.txt' in pipeline
    assert 'SCM comment "$REPO" "$PR" "$MSG"' in pipeline
    assert "no structured spec for the key" in check


def test_dashboard_pr_plan_controls_follow_default_off_flag(tmp_path):
    def render(value, name):
        path = tmp_path / name
        run = subprocess.run(
            [os.sys.executable, str(ROOT / "bin/dashboard.py")], cwd=ROOT,
            env={**os.environ, "AIQE_PR_PLAN": value,
                 "AIQE_DASHBOARD_OUT": str(path)},
            capture_output=True, text=True, encoding="utf-8", timeout=120,
            stdin=subprocess.DEVNULL)
        assert run.returncode == 0, run.stderr
        return path.read_text(encoding="utf-8")

    off = render("0", "off.html")
    on = render("1", "on.html")
    assert 'id="wz-start-pr-plan"' not in off
    assert "const PR_PLAN_ENABLED = false" in off
    assert 'id="wz-start-pr-plan"' in on
    assert "const PR_PLAN_ENABLED = true" in on
