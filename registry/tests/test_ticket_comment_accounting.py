"""JCTS-S3: every best-effort ticket comment leaves an explicit outcome."""
import json
import os
import pathlib
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
import event_log
import explain
import plan_state
import run_progress
import ticket_comment
import work_queue


def test_receipt_model_is_payload_free_and_closed():
    item = ticket_comment.receipt(
        "delivery", "PROJ-301", "posted", comment_id=" 10042 ",
        run_id="run-1", ts=1)
    assert item == {
        "kind": "delivery", "target": "PROJ-301", "comment_id": "10042",
        "outcome": "posted", "failure_detail": None, "run_id": "run-1",
        "ts": 1.0,
    }
    assert "body" not in item
    with pytest.raises(ValueError, match="outcome"):
        ticket_comment.receipt("delivery", "PROJ-301", "probably_posted")
    with pytest.raises(ValueError, match="target"):
        ticket_comment.receipt("delivery", "PROJ-301; rm -rf", "failed")


def _delivery(tmp_path, monkeypatch, completed, *, plan_key=None):
    attempts = tmp_path / "out/comment-attempts.jsonl"
    monkeypatch.setattr(ticket_comment, "ATTEMPTS", attempts)
    monkeypatch.setenv("AIQE_EVENTS_DIR", str(tmp_path / "events"))
    monkeypatch.setenv("RUN_ID", "run-42")
    monkeypatch.setattr(ticket_comment.settings_store, "load_env_into", lambda: None)
    monkeypatch.setattr(ticket_comment.work_queue, "git_bash_command",
                        lambda *args, **kwargs: (["tracker", "comment"], {}))
    monkeypatch.setattr(ticket_comment.subprocess, "run", lambda *args, **kwargs: completed)
    if plan_key:
        monkeypatch.setattr(plan_state, "DIR", tmp_path / "plans")
        monkeypatch.setattr(plan_state, "FILE", tmp_path / "plans/state.json")
    return ticket_comment.post(
        "plan" if plan_key else "delivery", "PROJ-301",
        "secret-bearing body must never be recorded", plan_key=plan_key)


def test_success_writes_scratch_event_and_plan_provenance(tmp_path, monkeypatch):
    item = _delivery(
        tmp_path, monkeypatch,
        subprocess.CompletedProcess([], 0, stdout="comment_id=10042\n", stderr=""),
        plan_key="PROJ-301")
    assert item["outcome"] == "posted" and item["comment_id"] == "10042"
    assert "secret-bearing" not in json.dumps(item)

    rows, corrupt = ticket_comment.read_attempts(
        tmp_path / "out/comment-attempts.jsonl", "run-42")
    assert rows == [item] and corrupt == 0
    stored = plan_state.get("PROJ-301")
    assert stored["comments"] == [item]

    events, corrupt = event_log.read(kinds={"ticket.comment"}, run_id="run-42")
    assert corrupt == 0 and len(events) == 1
    assert events[0]["detail"]["comment_outcome"] == "posted"
    assert "secret-bearing" not in json.dumps(events[0])


def test_failure_is_nonfatal_bounded_and_does_not_leak_adapter_output(
        tmp_path, monkeypatch):
    secret = "Bearer should-not-survive"
    item = _delivery(
        tmp_path, monkeypatch,
        subprocess.CompletedProcess([], 22, stdout="HTTP 401", stderr=secret))
    assert item["outcome"] == "failed"
    assert item["failure_detail"] == "tracker comment failed (exit 22, HTTP 401)"
    assert secret not in json.dumps(item)
    rows, _ = ticket_comment.read_attempts(tmp_path / "out/comment-attempts.jsonl")
    assert rows == [item]


def test_torn_receipt_is_counted_not_treated_as_no_attempt(tmp_path):
    path = tmp_path / "attempts.jsonl"
    good = ticket_comment.receipt("delivery", "PROJ-1", "posted", ts=1)
    path.write_text(json.dumps(good) + "\n{torn\n[]\n", encoding="utf-8")
    rows, corrupt = ticket_comment.read_attempts(path)
    assert rows == [good] and corrupt == 2


def test_jira_adapter_returns_the_posted_comment_id(tmp_path):
    stub = tmp_path / "bin"
    stub.mkdir()
    curl = stub / "curl"
    curl.write_text('#!/usr/bin/env bash\necho \'{"id":"4321"}\'\n',
                    encoding="utf-8")
    os.chmod(curl, 0o755)
    command, env = work_queue.git_bash_command(
        ROOT / "adapters/tracker/jira.sh", "comment", "PROJ-1", "plain text",
        prepend=[stub], JIRA_URL="https://jira.example.com",
        ATLASSIAN_MCP_TOKEN="synthetic-token")
    result = subprocess.run(
        command, cwd=ROOT, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL,
        timeout=60, check=False)
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "comment_id=4321"


def _record(root, comments, malformed=0):
    target = root / "reports/runs/r1.json"
    target.parent.mkdir(parents=True)
    target.write_text(json.dumps({
        "run_id": "r1", "trigger": {"type": "jira", "key": "PROJ-1"},
        "ts": 1, "overall": "no_changes", "gates": [], "phases": [],
        "comments": comments, "malformed_comment_lines": malformed,
    }), encoding="utf-8")


def test_failure_surfaces_in_progress_and_explain(tmp_path):
    failed = ticket_comment.receipt(
        "delivery", "PROJ-1", "failed",
        failure_detail="tracker comment failed (exit 22, HTTP 401)",
        run_id="r1", ts=1)
    _record(tmp_path, [failed], malformed=2)
    progress = run_progress.progress(key="PROJ-1", root=tmp_path)
    assert progress["comment_failures"] == [failed]
    assert progress["comment_records_corrupt"] == 2
    result = explain.explain(key="PROJ-1", root=tmp_path)
    notification = next(d for d in result["decisions"]
                        if d["id"] == "notification")
    assert "requester was not notified" in notification["answer"]
    assert "HTTP 401" in notification["answer"]
    integrity = next(u for u in result["unexplained"]
                     if u["id"] == "notification-integrity")
    assert "2 comment receipt line" in integrity["not_recorded"]


def test_malformed_legacy_corrupt_count_does_not_break_visibility(tmp_path):
    _record(tmp_path, [], malformed="not-a-number")
    assert run_progress.progress(key="PROJ-1", root=tmp_path)[
        "comment_records_corrupt"] == 0
    assert explain.explain(key="PROJ-1", root=tmp_path)["source"] == "record"


def test_pipeline_routes_all_ticket_comments_through_accounting_boundary():
    source = (ROOT / "engine/pipeline.sh").read_text(encoding="utf-8")
    assert "TRACKER comment" not in source
    for kind in ("routing_clarification", "requirements", "clarification",
                 "plan", "delivery"):
        assert f"TICKET_COMMENT {kind}" in source
    refusal = source[source.index('if [ "$REVIEW_POLICY_RC" -eq 78 ]'):]
    refusal = refusal[:refusal.index('elif [ "$REVIEW_POLICY_RC" -ne 0 ]')]
    assert refusal.index("TICKET_COMMENT delivery") < refusal.index("run_record.py")
    assert "out/comment-attempts.jsonl" in source


def test_run_record_and_dashboard_consume_the_same_receipts():
    record_source = (ROOT / "engine/lib/run_record.py").read_text(encoding="utf-8")
    dashboard_source = (ROOT / "bin/dashboard.py").read_text(encoding="utf-8")
    assert 'record["comments"] = comments' in record_source
    assert "commentFailures = p.comment_failures || []" in dashboard_source
    assert "Requester was not notified" in dashboard_source
