"""Regression tests for the JIRA test-plan approval workflow:
plan_state lifecycle, the plan-only / tests pipeline modes, and the CLI surface."""
import json, os, pathlib, subprocess, sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
import plan_state, work_queue

BASH = work_queue.bash_exe()
KEY = "ZZPLAN-1"


@pytest.fixture
def plan(tmp_path, monkeypatch):
    """Isolated plan store + plan dir so tests never touch real estate state."""
    monkeypatch.setattr(plan_state, "DIR", tmp_path / "plans")
    monkeypatch.setattr(plan_state, "FILE", tmp_path / "plans" / "state.json")
    monkeypatch.setattr(plan_state, "PLAN_DIR", tmp_path / "testplans")
    (tmp_path / "testplans").mkdir()
    plan_state.plan_path(KEY).write_text("# Test Plan\n\n## Scenarios\n- S1\n",
                                         encoding="utf-8")
    return tmp_path


def test_record_plan_starts_in_draft(plan):
    e = plan_state.record_plan(KEY, {"scenarios": [{"id": "S1"}]})
    assert e["status"] == "draft"
    assert plan_state.contract_path(KEY).exists()
    assert json.load(open(plan_state.contract_path(KEY)))["scenarios"][0]["id"] == "S1"


def test_full_lifecycle_draft_review_approved(plan):
    plan_state.record_plan(KEY)
    plan_state.set_status(KEY, "in_review", "qa-lead")
    assert plan_state.get(KEY)["status"] == "in_review"
    e = plan_state.set_status(KEY, "approved", "qa-lead", "looks good")
    assert e["status"] == "approved" and e["by"] == "qa-lead"
    # history is an append-only trail
    assert [h["status"] for h in e["history"]] == ["draft", "in_review", "approved"]


def test_changes_requested_then_reapproval(plan):
    plan_state.record_plan(KEY)
    plan_state.set_status(KEY, "changes_requested", "qa", "add negative cases")
    with pytest.raises(SystemExit, match="not approved"):
        plan_state.require_approved(KEY)
    plan_state.set_status(KEY, "approved", "qa")
    assert plan_state.require_approved(KEY)["status"] == "approved"


def test_editing_an_approved_plan_revokes_approval(plan):
    plan_state.record_plan(KEY)
    plan_state.set_status(KEY, "approved", "qa")
    e = plan_state.save_plan(KEY, "# Test Plan\n\n## Scenarios\n- S1\n- S2 (added)\n", "dev")
    assert e["status"] == "draft", "an edited plan must not keep its approval"
    assert "re-approval" in e["note"]
    assert "S2 (added)" in plan_state.plan_path(KEY).read_text(encoding="utf-8")
    with pytest.raises(SystemExit, match="not approved"):
        plan_state.require_approved(KEY)


def test_edit_while_in_review_keeps_status(plan):
    plan_state.record_plan(KEY)
    plan_state.set_status(KEY, "in_review", "qa")
    e = plan_state.save_plan(KEY, "# Test Plan\n\nedited\n", "qa")
    assert e["status"] == "in_review" and e["note"] == "plan edited"


def test_invalid_status_and_missing_plan_rejected(plan):
    plan_state.record_plan(KEY)
    with pytest.raises(SystemExit, match="status must be one of"):
        plan_state.set_status(KEY, "yolo")
    with pytest.raises(SystemExit, match="no test plan"):
        plan_state.set_status("NO-SUCH-KEY", "approved")
    with pytest.raises(SystemExit, match="no test plan"):
        plan_state.require_approved("NO-SUCH-KEY")
    with pytest.raises(SystemExit, match="empty"):
        plan_state.save_plan(KEY, "   ")


def test_link_and_generated_markers(plan):
    plan_state.record_plan(KEY)
    plan_state.set_status(KEY, "approved", "qa")
    e = plan_state.mark_linked(KEY, "attached PROJ-1-testplan.pdf", "qa")
    assert e["linked"]["ref"].startswith("attached")
    e = plan_state.mark_generated(KEY, "run-123")
    assert e["generated_run"] == "run-123"
    assert e["status"] == "approved"          # linking/generating never changes status
    s = [p for p in plan_state.summary() if p["key"] == KEY][0]
    assert s["linked"] is True and s["generated_run"] == "run-123" and s["has_plan"]


def test_state_file_is_outside_reports_runs():
    """Plan state must not land in reports/runs/ — every run-record glob there would
    otherwise have to learn to skip a 4th state file."""
    assert plan_state.FILE.parent.name == "plans"
    assert "runs" not in plan_state.FILE.parts[-3:-1]


# ----------------------------------------------------- CLI + pipeline integration

def _cli(args, env_extra):
    env = {**os.environ, **env_extra}
    return subprocess.run([sys.executable, str(ROOT / "bin/qa.py"), "plan", *args],
                          cwd=ROOT, capture_output=True, text=True,
                          stdin=subprocess.DEVNULL, env=env)


@pytest.fixture
def cli_env(tmp_path):
    """Point the CLI's plan store + plan dir at scratch space."""
    (tmp_path / "testplans").mkdir()
    (tmp_path / "testplans" / f"{KEY}.md").write_text(
        "# Test Plan\n\n## Scenarios\n- S1 boundary\n", encoding="utf-8")
    return {"AIQE_PLAN_DIR": str(tmp_path / "plans"),
            "AIQE_TESTPLAN_DIR": str(tmp_path / "testplans")}


def test_cli_review_approve_and_show(cli_env):
    assert _cli(["review", KEY, "--by", "qa"], cli_env).returncode == 0
    r = _cli(["approve", KEY, "--by", "qa", "--note", "ok"], cli_env)
    assert r.returncode == 0 and "approved" in r.stdout
    assert "make plan-tests" in r.stdout            # tells the user the next step
    r = _cli(["show", KEY], cli_env)
    assert r.returncode == 0 and "status: approved" in r.stdout
    assert "S1 boundary" in r.stdout                # prints the plan body
    r = _cli(["list"], cli_env)
    assert KEY in r.stdout and "approved" in r.stdout


def test_cli_request_changes_blocks_link(cli_env):
    _cli(["request-changes", KEY, "--by", "qa", "--note", "add negatives"], cli_env)
    r = _cli(["link", KEY], cli_env)                # link requires approval
    assert r.returncode != 0
    assert "not approved" in (r.stdout + r.stderr)


def test_cli_edit_revokes_approval(cli_env, tmp_path):
    _cli(["approve", KEY, "--by", "qa"], cli_env)
    newfile = tmp_path / "new.md"
    newfile.write_text("# Test Plan\n\n## Scenarios\n- S1\n- S2 new\n", encoding="utf-8")
    r = _cli(["edit", KEY, "--file", str(newfile), "--by", "dev"], cli_env)
    assert r.returncode == 0 and "draft" in r.stdout
    assert "status: draft" in _cli(["show", KEY], cli_env).stdout


def test_pipeline_tests_mode_refuses_unapproved_plan_before_doing_work(tmp_path):
    """The approval gate must fire BEFORE cloning/LLM work, not after."""
    env = {**os.environ, "AIQE_MOCK": "1",
           "AIQE_PLAN_DIR": str(tmp_path / "plans"),
           "AIQE_TESTPLAN_DIR": str(tmp_path / "testplans")}
    r = subprocess.run([BASH, "engine/pipeline.sh", "tests", "NOPLAN-1"],
                       cwd=ROOT, capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, env=env)
    assert r.returncode != 0
    assert "no test plan" in (r.stdout + r.stderr)
    # it must not have started the phase chain
    assert "GATE_STATUS" not in r.stdout


def test_pipeline_exposes_plan_and_tests_modes():
    text = (ROOT / "engine/pipeline.sh").read_text(encoding="utf-8")
    assert "pr|jira|plan|tests" in text
    assert 'MODE" = "plan"' in text and 'MODE" = "tests"' in text
    assert "require-approved" in text               # the gate is wired in
    assert "PLAN_STATUS=DRAFT" in text


def test_dashboard_static_snapshot_shows_plans():
    """The static snapshot (make dashboard, no server) must render real plan rows —
    every other view server-renders, and an empty Plans view read as 'no plans'."""
    subprocess.run([sys.executable, str(ROOT / "bin/dashboard.py")], cwd=ROOT,
                   check=True, capture_output=True, stdin=subprocess.DEVNULL)
    page = (ROOT / "reports/dashboard.html").read_text(encoding="utf-8")
    import re
    table = re.search(r'<table id="plans-table">.*?</table>', page, re.S)
    assert table, "plans table missing"
    body = table.group(0)
    import plan_state as ps
    plans = ps.summary()
    if plans:                                   # real state -> rows, not a placeholder
        assert plans[0]["key"] in body
        assert "plan-open" in body              # the Review button is server-rendered
    assert 'id="plans-count"' in page


def test_dashboard_keeps_navigation_on_narrow_screens():
    """The sidebar used to be display:none under 900px, stranding the user with no
    way to change view. It must collapse to a nav strip instead."""
    page = (ROOT / "reports/dashboard.html").read_text(encoding="utf-8")
    import re
    mq = re.search(r"@media \(max-width: 900px\) \{(.*?)\n\}", page, re.S)
    assert mq, "narrow-screen media query missing"
    block = mq.group(1)
    assert "aside { display:none" not in block.replace(" ", " ")
    assert "flex-direction:row" in block        # nav becomes a horizontal strip
    assert "overflow-x:auto" in block           # …and scrolls when it overflows
