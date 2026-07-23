"""Flow-2 requirement: the system reads the JIRA DESCRIPTION AND COMMENTS before
authoring a test plan.

The analyze prompt always promised comments in out/ticket.json, but nothing put them
there: the real adapter never requested the field, the mock ticket had none, and
inline (pasted) tickets dropped the thread. Comments are where clarifications and
edge cases live — a plan authored without them tests the wrong scope.
"""
import json, os, pathlib, subprocess, sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
import inline_ticket
import work_queue


# ------------------------------------------------------------------ mock ticket

def test_mock_ticket_carries_comments():
    t = json.loads((ROOT / "eval/benchmark/tickets/.item-PROJ-301.json")
                   .read_text(encoding="utf-8"))
    assert t.get("comments"), "demo ticket must carry comments so plans exercise them"
    bodies = " ".join(c["body"] for c in t["comments"])
    # the comments must MATTER to the plan: a scope clarification + an edge case
    assert "OUT OF SCOPE" in bodies and "rounding" in bodies
    assert all(c.get("author") and c.get("body") for c in t["comments"])


def test_mock_tracker_get_item_returns_the_comments():
    r = subprocess.run([work_queue.bash_exe(), str(ROOT / "adapters/mock/tracker.sh"),
                        "get_item", "PROJ-301"], cwd=ROOT, capture_output=True,
                       text=True, encoding="utf-8", errors="replace",
                       stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stderr
    assert len(json.loads(r.stdout).get("comments", [])) >= 2


# ------------------------------------------------------------- real adapter

V3_ISSUE = {
    "key": "REAL-9",
    "fields": {
        "summary": "ADF ticket",
        # Jira Cloud v3: description and comment bodies are ADF documents
        "description": {"type": "doc", "content": [
            {"type": "paragraph", "content": [
                {"type": "text", "text": "As a shopper I cannot apply invalid discounts."}]}]},
        "components": [{"name": "Checkout"}], "labels": ["api-only"],
        "fixVersions": [{"name": "2026.08"}], "issuetype": {"name": "Story"},
        "comment": {"comments": [
            {"author": {"displayName": "PM Jane"}, "created": "2026-07-20T10:00:00Z",
             "body": {"type": "doc", "content": [
                 {"type": "paragraph", "content": [
                     {"type": "text", "text": "stacking is "},
                     {"type": "text", "text": "out of scope"}]}]}},
            {"author": {"name": "dev.omar"}, "created": "2026-07-21T11:00:00Z",
             "body": "plain v2 string body"},          # Server/DC shape
        ]},
    },
}


def _run_get_item(tmp_path, issue):
    """Run the real adapter with a stubbed curl that returns `issue`."""
    stub = tmp_path / "bin"
    stub.mkdir()
    payload = tmp_path / "issue.json"
    payload.write_text(json.dumps(issue), encoding="utf-8")
    (stub / "curl").write_text(f'#!/usr/bin/env bash\ncat "{payload.as_posix()}"\n',
                               encoding="utf-8")
    os.chmod(stub / "curl", 0o755)
    env = {**os.environ, "PATH": f"{stub}{os.pathsep}{os.environ['PATH']}",
           "JIRA_URL": "https://jira.example.com", "ATLASSIAN_MCP_TOKEN": "tok"}
    return subprocess.run([work_queue.bash_exe(), str(ROOT / "adapters/tracker/jira.sh"),
                           "get_item", issue["key"]], cwd=ROOT, capture_output=True,
                          text=True, encoding="utf-8", errors="replace",
                          stdin=subprocess.DEVNULL, env=env)


def test_real_get_item_flattens_adf_and_v2_comments(tmp_path):
    r = _run_get_item(tmp_path, V3_ISSUE)
    assert r.returncode == 0, r.stderr
    t = json.loads(r.stdout)
    # description: flattened ADF text, not a python-dict repr
    assert t["description"] == "As a shopper I cannot apply invalid discounts."
    assert "{'type'" not in t["description"]
    # comments: both body shapes, author from displayName OR name
    assert [c["author"] for c in t["comments"]] == ["PM Jane", "dev.omar"]
    assert t["comments"][0]["body"] == "stacking is out of scope"
    assert t["comments"][1]["body"] == "plain v2 string body"


def test_real_get_item_caps_the_comment_thread(tmp_path):
    """A years-old ticket with hundreds of comments must not blow the phase budget."""
    issue = json.loads(json.dumps(V3_ISSUE))
    issue["fields"]["comment"]["comments"] = [
        {"author": {"name": f"u{i}"}, "created": "", "body": f"c{i}"} for i in range(50)]
    t = json.loads(_run_get_item(tmp_path, issue).stdout)
    assert len(t["comments"]) == 20
    assert t["comments"][-1]["body"] == "c49", "must keep the LATEST comments"


def test_real_get_item_requests_the_comment_field():
    src = (ROOT / "adapters/tracker/jira.sh").read_text(encoding="utf-8")
    assert "comment" in src.split("get_item", 1)[1][:400], \
        "get_item must request the comment field explicitly"


# ------------------------------------------------------------- inline tickets

def test_inline_ticket_splits_a_pasted_comment_thread():
    t = inline_ticket.build(
        "Bug: rounding wrong\nAC-1: totals are integers\n\nComments:\n"
        "PM: stacking out of scope\n- Dev: pct 33 on 100 -> 67", key="T-C1")
    assert [c["body"] for c in t["comments"]] == \
        ["PM: stacking out of scope", "Dev: pct 33 on 100 -> 67"]
    assert "Comments:" not in t["description"], "thread must not stay in the description"
    assert t["acceptance_criteria"] == ["AC-1: totals are integers"]


def test_inline_ticket_without_comments_still_has_the_field():
    t = inline_ticket.build("Just a story\nAC-1: works", key="T-C2")
    assert t["comments"] == []


# ------------------------------------------------------------------ plumbing

def test_analyze_phase_receives_the_ticket_and_prompt_names_comments():
    """End of the chain: the pipeline passes out/ticket.json to analyze, and the
    prompt tells the model comments are part of the requirements input."""
    pipeline = (ROOT / "engine/pipeline.sh").read_text(encoding="utf-8")
    assert "jira-analyze.md AGENTS.md out/issue-guidance.md out/ticket.json" in pipeline
    prompt = (ROOT / "prompts/jira-analyze.md").read_text(encoding="utf-8")
    assert "comments" in prompt
