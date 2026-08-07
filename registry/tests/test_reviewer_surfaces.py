"""B4 verdict surfaces: one snapshot, everywhere, never a human decision."""
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
sys.path.insert(0, str(ROOT / "bin"))

import explain
import pr_comment
import qa
import run_progress
import test_reviewer as reviewer


def _finding():
    return {"repo": "api-tests", "severity": "high",
            "category": "missing_coverage", "file": "suites/refund.spec.ts",
            "test": "refund ceiling", "finding": "No captured-total boundary.",
            "fix": "Add the equal and over-captured cases."}


def _signal(verdict="needs_work"):
    findings = [_finding()] if verdict == "needs_work" else []
    return {"artifact": "test-reviewer", "schema": 1, "state": "reviewed",
            "verdict": verdict,
            "repos": [{"repo": "api-tests", "state": "reviewed",
                       "verdict": verdict, "findings": [
                           {k: v for k, v in f.items() if k != "repo"}
                           for f in findings], "simulated": False}],
            "findings": findings, "simulated": False}


def _review():
    return reviewer.surface(_signal(), cfg={"enabled": True, "agent_gate": "warn"})


def test_snapshot_records_policy_and_initial_unresolved_findings(monkeypatch):
    monkeypatch.delenv("AIQE_TEST_REVIEWER", raising=False)
    value = _review()
    assert set(("verdict", "findings", "loops", "unresolved", "policy")) <= set(value)
    assert value["verdict"] == "needs_work" and value["policy"] == "warn"
    assert value["loops"] == 0 and value["unresolved"] == value["findings"]


def test_disabled_and_missing_enabled_results_are_distinct(monkeypatch):
    monkeypatch.delenv("AIQE_TEST_REVIEWER", raising=False)
    assert reviewer.surface(None, cfg={"enabled": False})["verdict"] == "skipped"
    assert reviewer.surface(None, cfg={"enabled": True})["verdict"] == "unavailable"


def test_comment_and_explain_render_the_same_evidence(tmp_path):
    record = {"run_id": "r-b4", "ts": 1,
              "trigger": {"type": "pr", "key": "PR-x-4"},
              "overall": "committed", "review": _review(),
              "phases": [
                  {"name": "triage", "contract": {"impact": "create"}},
                  {"name": "generate", "contract": {"tests": [
                      {"file": "suites/refund.spec.ts", "action": "created"}]}},
                  {"name": "validate", "contract": {"passed": 1, "failed": 0}}],
              "gates": [{"test_repo": "api-tests", "status": "committed",
                         "exit_code": 0, "commit": "abcdef12"}]}
    markdown = pr_comment.from_record(record)
    assert "agent review: needs_work" in markdown
    assert "1 finding(s), 1 unresolved, 0 repair loop(s); policy: warn" in markdown
    runs = tmp_path / "reports/runs"
    runs.mkdir(parents=True)
    (runs / "r-b4.json").write_text(json.dumps(record), encoding="utf-8")
    result = explain.explain(run_id="r-b4", root=tmp_path)
    decision = next(d for d in result["decisions"] if d["id"] == "review")
    assert decision["answer"] == "needs_work under policy warn"
    assert any("No captured-total boundary" in item for item in decision["because"])
    assert any("0 review repair loop" in item and "1 finding(s) survived" in item
               for item in decision["because"])


def test_run_progress_places_review_between_validation_and_gate(tmp_path):
    runs = tmp_path / "reports/runs"
    runs.mkdir(parents=True)
    record = {"run_id": "r-b4", "mode": "pr", "ts": 1,
              "trigger": {"type": "pr", "key": "PR-x-4"},
              "overall": "committed", "review": _review(),
              "phases": [{"name": name, "contract": {}}
                         for name in ("resolve", "triage", "generate", "validate", "critic")],
              "gates": [{"test_repo": "api-tests", "status": "committed",
                         "exit_code": 0}]}
    (runs / "r-b4.json").write_text(json.dumps(record), encoding="utf-8")
    steps = run_progress.progress(run_id="r-b4", root=tmp_path)["steps"]
    ids = [step["id"] for step in steps]
    assert ids.index("validate") < ids.index("review") < ids.index("gate")
    review_step = next(step for step in steps if step["id"] == "review")
    assert review_step["state"] == "done" and "needs_work" in review_step["detail"]


def test_pipeline_posts_one_jira_line_and_never_changes_human_status():
    source = (ROOT / "engine/pipeline.sh").read_text(encoding="utf-8")
    summary = source.index("REVIEW_LINE=$(python3 engine/lib/test_reviewer.py summary")
    jira = source.index('case "$MODE" in jira|tests) TRACKER comment')
    assert summary < jira
    dashboard = (ROOT / "bin/dashboard.py").read_text(encoding="utf-8")
    assert "agent_review_cell" in dashboard and "team review" in dashboard
    assert "set_status" not in dashboard[dashboard.index("agent_review_cell"):
                                         dashboard.index("agent_review_cell") + 2000]
    cli = (ROOT / "bin/qa.py").read_text(encoding="utf-8")
    assert "'agent review':<18" in cli and "test_reviewer.recorded" in cli


def test_cli_board_keeps_agent_and_human_verdicts_side_by_side(
        tmp_path, monkeypatch, capsys):
    run = tmp_path / "run.json"
    run.write_text(json.dumps({
        "run_id": "r-b4", "ts": 1,
        "trigger": {"type": "pr", "key": "PR-x-4"},
        "review": _review(),
    }), encoding="utf-8")
    monkeypatch.setattr(qa, "_run_record_files", lambda: [str(run)])
    monkeypatch.setattr(qa.review_state, "load", lambda: {
        "PR-x-4": {"status": "approved", "reviewer": "human", "updated": 1}
    })
    qa.cmd_reviews(None)
    rendered = capsys.readouterr().out
    assert "agent review" in rendered
    assert "approved" in rendered and "needs_work (1)" in rendered


def test_summary_is_bounded_and_total_for_persisted_data():
    line = reviewer.summary_line({"verdict": "approve\nforged", "policy": "warn\nforged",
                                  "findings": "bad", "unresolved": None,
                                  "loops": "not-an-int"})
    assert "\n" not in line and "0 finding(s)" in line and "0 repair loop(s)" in line
