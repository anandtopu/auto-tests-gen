"""Regression tests for OpenHands webhook ingestion (engine/lib/openhands_events.py
and the receiver routes). The receiver must record agent activity, tolerate schema
drift between OpenHands versions, and never let an agent enqueue pipeline work."""
import json, os, pathlib, subprocess, sys, threading, time
import urllib.error, urllib.request

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
import openhands_events as oe


@pytest.fixture
def store(tmp_path, monkeypatch):
    monkeypatch.setattr(oe, "DIR", tmp_path / "openhands")
    monkeypatch.setattr(oe, "FILE", tmp_path / "openhands" / "state.json")
    return tmp_path


# ------------------------------------------------------------------ ingestion

def test_event_batch_is_recorded_per_conversation(store):
    r = oe.record_events([
        {"conversation_id": "c1", "kind": "AgentStateChanged", "status": "running"},
        {"conversation_id": "c1", "kind": "MessageAction"},
        {"conversation_id": "c2", "kind": "MessageAction"},
    ])
    assert r["accepted"] == 3 and r["conversations"] == ["c1", "c2"]
    s = {x["conversation_id"]: x for x in oe.summary()}
    assert s["c1"]["event_count"] == 2 and s["c1"]["status"] == "running"
    assert s["c2"]["event_count"] == 1


def test_accepts_the_shapes_openhands_actually_sends(store):
    # a bare list, a single object, and {"events": [...]} must all work
    assert oe.record_events([{"conversation_id": "a", "kind": "x"}])["accepted"] == 1
    assert oe.record_events({"conversation_id": "b", "kind": "x"})["accepted"] == 1
    assert oe.record_events({"events": [{"conversation_id": "c", "kind": "x"},
                                        {"conversation_id": "c", "kind": "y"}]}
                            )["accepted"] == 2
    assert {x["conversation_id"] for x in oe.summary()} >= {"a", "b", "c"}


def test_tolerates_schema_drift_in_field_names(store):
    """Version drift: conversationId/sessionId/id, type/kind, state/status."""
    oe.record_events([{"conversationId": "cam", "type": "Obs", "state": "running"}])
    oe.record_events([{"sessionId": "snake", "event_type": "Act", "agent_state": "finished"}])
    oe.record_events([{"id": "plain", "action": "run"}])
    s = {x["conversation_id"]: x for x in oe.summary()}
    assert s["cam"]["status"] == "running"
    assert s["snake"]["status"] == "finished" and s["snake"]["terminal"]
    assert "plain" in s


def test_unrecognised_payload_is_counted_not_rejected(store):
    r = oe.record_events([{"something": "totally-unexpected"}, "not-a-dict", 42])
    assert r["accepted"] == 1                     # the dict counts, the rest are skipped
    assert oe.summary()[0]["conversation_id"] == "unknown"


def test_conversation_lifecycle_records_repo_and_error(store):
    oe.record_conversation({"conversation_id": "c9", "status": "RUNNING",
                            "selected_repository": "org/ai-qe-control"})
    oe.record_conversation({"conversation_id": "c9", "status": "error",
                            "error_message": "sandbox died"})
    e = oe.get("c9")
    assert e["repo"] == "org/ai-qe-control"
    assert e["status"] == "error" and "sandbox died" in e["error"]
    assert oe.summary()[0]["terminal"] is True


def test_store_stays_bounded(store, monkeypatch):
    monkeypatch.setattr(oe, "MAX_CONVERSATIONS", 5)
    monkeypatch.setattr(oe, "MAX_EVENTS_PER_CONV", 3)
    for i in range(12):
        oe.record_events([{"conversation_id": f"c{i}", "kind": "x"}])
    assert len(oe.load()) <= 5, "conversation store must not grow without bound"
    for i in range(10):
        oe.record_events([{"conversation_id": "chatty", "kind": f"e{i}"}])
    e = oe.get("chatty")
    assert len(e["events"]) == 3 and e["event_count"] == 10   # trail capped, count kept


def test_prune_drops_only_old_terminal_conversations(store):
    oe.record_conversation({"conversation_id": "done", "status": "finished"})
    oe.record_conversation({"conversation_id": "live", "status": "running"})
    st = oe.load()
    st["done"]["updated"] = time.time() - 48 * 3600
    oe._save(st)
    r = oe.prune(keep_terminal_hours=24)
    assert r["removed"] == 1
    assert "live" in oe.load() and "done" not in oe.load()


# ------------------------------------------------------------------ receiver

def _serve(env_extra, tmp_path):
    """Start the receiver on a free port with an isolated store."""
    import socket
    s = socket.socket(); s.bind(("127.0.0.1", 0)); port = s.getsockname()[1]; s.close()
    env = {**os.environ, "AIQE_HOOK_PORT": str(port),
           "AIQE_OPENHANDS_DIR": str(tmp_path / "oh"),
           "AIQE_HOOKS_SEEN": str(tmp_path / "seen.json"),
           "AIQE_QUEUE_FILE": str(tmp_path / "queue.json"), **env_extra}
    p = subprocess.Popen([sys.executable, str(ROOT / "bin/taskevent_receiver.py")],
                         cwd=ROOT, env=env, stdout=subprocess.DEVNULL,
                         stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    for _ in range(60):
        try:
            urllib.request.urlopen(base + "/healthz", timeout=2).read()
            return p, base
        except OSError:
            time.sleep(0.2)
    p.kill()
    pytest.skip("receiver did not start")


def _post(base, path, payload, headers=None):
    req = urllib.request.Request(base + path, data=json.dumps(payload).encode(),
                                 headers={"Content-Type": "application/json",
                                          **(headers or {})})
    try:
        r = urllib.request.urlopen(req, timeout=10)
        return r.status, json.load(r)
    except urllib.error.HTTPError as e:
        return e.code, json.load(e)


def test_receiver_routes_and_bearer_auth(tmp_path):
    p, base = _serve({"AIQE_HOOK_TOKEN": "sekret"}, tmp_path)
    try:
        # healthz advertises the new endpoints
        health = json.load(urllib.request.urlopen(base + "/healthz", timeout=5))
        assert any("openhands/events" in e for e in health["endpoints"])
        # no credentials -> 401
        assert _post(base, "/hooks/openhands/events", [{"conversation_id": "x"}])[0] == 401
        # Bearer works (the only header form WebhookSpec can express)
        code, body = _post(base, "/hooks/openhands/events",
                           [{"conversation_id": "c1", "kind": "Msg", "status": "running"}],
                           {"Authorization": "Bearer sekret"})
        assert code == 200 and body["ok"] and body["accepted"] == 1
        # X-AIQE-Token still works for the existing senders
        code, body = _post(base, "/hooks/openhands/conversations",
                           {"conversation_id": "c1", "status": "finished"},
                           {"X-AIQE-Token": "sekret"})
        assert code == 200 and body["ok"]
    finally:
        p.kill()


def test_openhands_events_never_enqueue_work(tmp_path):
    """Boundary: agent chatter is observability, not a trigger. Includes a positive
    control so the 'queue stayed empty' assertion can't pass vacuously."""
    p, base = _serve({}, tmp_path)
    q = tmp_path / "queue.json"
    try:
        # even a payload that LOOKS like a TaskEvent must not enqueue on this route
        for payload in ([{"conversation_id": "c", "kind": "x"}],
                        {"mode": "pr", "repo": "orders-api", "pr": 201}):
            assert _post(base, "/hooks/openhands/events", payload)[0] == 200
        assert not q.exists() or json.loads(q.read_text(encoding="utf-8")) == [], \
            "OpenHands events must never create queue items"
        # positive control: the real trigger route DOES enqueue into this same store
        code, body = _post(base, "/hooks/taskevent",
                           {"mode": "pr", "repo": "orders-api", "pr": 201,
                            "updated": "control"})
        assert code == 200 and body["accepted"], body
        assert q.exists() and len(json.loads(q.read_text(encoding="utf-8"))) == 1, \
            "the queue store under test must actually be writable"
    finally:
        p.kill()


def test_malformed_openhands_payload_returns_200_not_500(tmp_path):
    """A 5xx would just make OpenHands retry the same bad batch forever."""
    p, base = _serve({}, tmp_path)
    try:
        for payload in ("just a string", 12345, {"deeply": {"nested": [1, 2]}}, []):
            code, _ = _post(base, "/hooks/openhands/events", payload)
            assert code == 200, f"payload {payload!r} produced {code}"
    finally:
        p.kill()


def test_state_file_is_outside_reports_runs():
    assert oe.FILE.parent.name == "openhands"
    assert "runs" not in oe.FILE.parts[-3:-1]
