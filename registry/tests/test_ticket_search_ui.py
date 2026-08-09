"""JCTS-S2: structured Intake UI/API and queue attribute handoff."""
import json
import os
import pathlib
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
sys.path.insert(0, str(ROOT / "bin"))
import dashboard_server
import work_queue


def test_http_filter_translation_is_closed_and_unambiguous():
    assert dashboard_server.ticket_filters(
        "release=2.14&issue_type=Bug&component=Checkout&label=api-only&"
        "status=Open&text=refund") == {
            "fixversion": "2.14", "issue_type": "Bug", "component": "Checkout",
            "label": "api-only", "status": "Open", "text": "refund",
        }
    with pytest.raises(ValueError, match="unsupported"):
        dashboard_server.ticket_filters("jql=project%3DSEC")
    with pytest.raises(ValueError, match="appear once"):
        dashboard_server.ticket_filters("label=a&label=b")
    with pytest.raises(ValueError, match="not both"):
        dashboard_server.ticket_filters("release=1&fixversion=1")


def test_server_rejects_a_lying_adapter_envelope(monkeypatch):
    monkeypatch.setattr(dashboard_server, "_tracker_json", lambda *args: {
        "items": [{"key": "PROJ-1"}], "returned": 50, "total": 140,
    })
    with pytest.raises(RuntimeError, match="page counts"):
        dashboard_server.jira_search({"label": "api-only"})

    monkeypatch.setattr(dashboard_server, "_tracker_json", lambda *args: {
        "items": [{"key": "PROJ-1", "components": "not-a-list"}],
        "returned": 1, "total": 1,
    })
    with pytest.raises(RuntimeError, match="components must be a string list"):
        dashboard_server.jira_search({"label": "api-only"})


def test_queue_metadata_is_bounded_and_legacy_reads_remain_valid(tmp_path, monkeypatch):
    monkeypatch.setattr(work_queue, "FILE", tmp_path / "queue.json")
    item, fresh = work_queue.add(
        "jira", "PROJ-301", release="2026.08", issue_type=" Story ",
        components=["Checkout"], labels=["api-only"], fix_version="2026.08")
    assert fresh
    assert item["issue_type"] == "Story"
    assert item["components"] == ["Checkout"]
    assert item["labels"] == ["api-only"]
    assert item["fix_version"] == "2026.08"

    legacy = {"id": "old", "mode": "jira", "target": "OLD-1", "pr": None,
              "release": "", "requested_by": "", "status": "queued", "ts": 1}
    work_queue.save([legacy])
    loaded = work_queue.load()[0]
    assert loaded.get("issue_type", "") == ""
    assert loaded.get("components", []) == []
    assert loaded.get("labels", []) == []
    assert loaded.get("fix_version", "") == ""

    work_queue.save([])
    plain, _ = work_queue.add("jira", "PLAIN-1")
    assert not ({"issue_type", "components", "labels", "fix_version"} & set(plain))


@pytest.mark.parametrize(("field", "value", "message"), [
    ("components", "Checkout", "must be a list"),
    ("labels", ["x"] * 51, "too many"),
    ("issue_type", "x" * 101, "too long"),
    ("fix_version", 214, "must be a string"),
])
def test_queue_metadata_rejects_malformed_or_unbounded_values(
        tmp_path, monkeypatch, field, value, message):
    monkeypatch.setattr(work_queue, "FILE", tmp_path / "queue.json")
    with pytest.raises(SystemExit, match=message):
        work_queue.add("jira", "PROJ-301", **{field: value})
    assert work_queue.load() == []


def test_duplicate_queue_submission_still_validates_metadata(tmp_path, monkeypatch):
    monkeypatch.setattr(work_queue, "FILE", tmp_path / "queue.json")
    work_queue.add("jira", "PROJ-301")
    with pytest.raises(SystemExit, match="components must be a list"):
        work_queue.add("jira", "PROJ-301", components="Checkout")


def test_queue_runner_refetches_and_does_not_pass_display_metadata(
        tmp_path, monkeypatch):
    queue_file = tmp_path / "queue.json"
    monkeypatch.setattr(work_queue, "FILE", queue_file)
    work_queue.add("jira", "PROJ-301", issue_type="Bug",
                   components=["Stale"], labels=["old"], fix_version="1.0")
    seen = {}

    def command(script, *args, **kwargs):
        seen["script"], seen["args"] = pathlib.Path(script), args
        return ["normalized-pipeline"], os.environ.copy()

    monkeypatch.setattr(work_queue, "git_bash_command", command)
    monkeypatch.setattr(work_queue.subprocess, "run", lambda *args, **kwargs:
                        subprocess.CompletedProcess(args[0], 0, stdout="", stderr=""))
    work_queue.run_all()
    assert seen["script"].name == "pipeline.sh"
    assert seen["args"] == ("jira", "PROJ-301")


def _render(tmp_path, enabled):
    output = tmp_path / ("search-on.html" if enabled else "search-off.html")
    env = dict(os.environ, AIQE_TICKET_SEARCH="1" if enabled else "0",
               AIQE_DASHBOARD_OUT=str(output))
    result = subprocess.run(
        [sys.executable, str(ROOT / "bin/dashboard.py")], cwd=ROOT, env=env,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        stdin=subprocess.DEVNULL, timeout=300, check=False)
    assert result.returncode == 0, result.stderr
    return output.read_text(encoding="utf-8")


def test_feature_flag_controls_ui_and_bulk_uses_individual_intake(tmp_path):
    disabled = _render(tmp_path, False)
    enabled = _render(tmp_path, True)
    for marker in ('id="fetch-type"', 'id="fetch-component"', 'id="fetch-label"',
                   'id="fetch-status"', 'id="fetch-text"',
                   'id="fetch-queue-all"', '<th>ticket attributes</th>'):
        assert marker not in disabled
        assert marker in enabled
    assert "const TICKET_SEARCH_ENABLED = false" in disabled
    assert "const TICKET_SEARCH_ENABLED = true" in enabled
    assert "Queue ' + fetchedState.returned + ' of ' + fetchedState.total + ' matched?" in enabled
    assert "for (const item of tickets) await queueFetchedItem(item, 'jira')" in enabled
    assert "Search failed: " in enabled
    assert "search failed — results are not current" in enabled

    script = max(__import__("re").findall(r"<script>(.*?)</script>", enabled,
                                           __import__("re").S), key=len)
    js = tmp_path / "ticket-search.js"
    js.write_text(script, encoding="utf-8")
    checked = subprocess.run(["node", "--check", str(js)], capture_output=True,
                             text=True, encoding="utf-8", timeout=60, check=False,
                             stdin=subprocess.DEVNULL)   # Windows: see CLAUDE.md
    assert checked.returncode == 0, checked.stderr


def _free_port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    port = sock.getsockname()[1]
    sock.close()
    return port


def _request(url, method="GET", payload=None):
    data = json.dumps(payload).encode() if payload is not None else None
    request = urllib.request.Request(
        url, data=data, method=method,
        headers={"Content-Type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(request, timeout=15) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read())


def test_live_mock_search_and_queue_handoff(tmp_path):
    port = _free_port()
    env = dict(os.environ, AIQE_MOCK="1", AIQE_TICKET_SEARCH="1",
               AIQE_UI_PORT=str(port), AIQE_UI_TOKEN="",
               AIQE_QUEUE_FILE=str(tmp_path / "queue.json"),
               AIQE_EVENTS_DIR=str(tmp_path / "events"),
               AIQE_DASHBOARD_OUT=str(tmp_path / "dashboard.html"))
    process = subprocess.Popen(
        [sys.executable, str(ROOT / "bin/dashboard_server.py")], cwd=ROOT, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        stdin=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    try:
        for _ in range(80):
            try:
                status, _ = _request(base + "/api/items?release=1999.01")
                if status:
                    break
            except (OSError, urllib.error.URLError):
                time.sleep(0.2)
        query = urllib.parse.urlencode({
            "release": "2026.08", "issue_type": "Story",
            "component": "Checkout", "label": "api-only",
            "status": "In Progress", "text": "rounding",
        })
        status, result = _request(base + "/api/items?" + query)
        assert status == 200
        assert result["returned"] == result["total"] == 1
        ticket = result["items"][0]
        assert ticket["key"] == "PROJ-301"
        assert ticket["components"] == ["Checkout"]

        status, error = _request(base + "/api/items?raw_jql=project%3DSEC")
        assert status == 400 and "unsupported" in error["error"]

        status, queued = _request(base + "/api/queue", "POST", {
            "mode": "jira", "target": ticket["target"],
            "release": ticket["release"], "issue_type": ticket["issue_type"],
            "components": ticket["components"], "labels": ticket["labels"],
            "fix_version": ticket["fix_version"],
        })
        assert status == 200 and queued["queued"] is True
        stored = json.loads((tmp_path / "queue.json").read_text(encoding="utf-8"))[0]
        assert stored["issue_type"] == "Story"
        assert stored["components"] == ["Checkout"]
        assert stored["labels"] == ["api-only"]
        assert stored["fix_version"] == "2026.08"
    finally:
        process.terminate()
        try:
            process.wait(timeout=10)
        except subprocess.TimeoutExpired:
            process.kill()
