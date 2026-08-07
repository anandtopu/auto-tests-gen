"""PRD v2 A1.7: optional PR ticket linkage on normalized TaskEvents."""
import hashlib
import importlib.util
import json
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[2]
SPEC = importlib.util.spec_from_file_location(
    "aiqe_taskevent_receiver_a17", ROOT / "bin/taskevent_receiver.py")
receiver = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(receiver)


def _state(tmp_path, monkeypatch):
    monkeypatch.setattr(receiver, "SEEN_FILE", tmp_path / "seen.json")
    monkeypatch.setattr(receiver.work_queue, "FILE", tmp_path / "queue.json")


def test_schema_pins_pr_key_optional_and_jira_key_required():
    schema = json.loads(
        (ROOT / "triggers/task-event-schema.json").read_text(encoding="utf-8"))
    description = schema["properties"]["key"]["description"]
    assert "optional explicit ticket linkage for pr mode" in description
    assert "excluded from PR replay identity" in description

    pr_rule, jira_rule = schema["allOf"]
    assert pr_rule["if"]["properties"]["mode"]["const"] == "pr"
    assert pr_rule["then"]["required"] == ["repo", "pr"]
    assert "key" not in pr_rule["then"]["required"]
    assert jira_rule["if"]["properties"]["mode"]["const"] == "jira"
    assert "key" in jira_rule["then"]["required"]


def test_pr_key_is_excluded_from_the_exact_historical_replay_digest():
    event = {"mode": "pr", "repo": "orders-api", "pr": 201,
             "updated": "sha1", "workflow_version": "1"}
    historical = hashlib.sha256(b"pr|orders-api|201||sha1|1").hexdigest()
    assert receiver.idempotency_key(event) == historical
    assert receiver.idempotency_key({**event, "key": "PROJ-301"}) == historical

    jira = {"mode": "jira", "key": "PROJ-301", "updated": "t1"}
    assert receiver.idempotency_key(jira) != receiver.idempotency_key(
        {**jira, "key": "OTHER-9"})


def test_pr_event_forwards_ticket_and_keyed_unkeyed_redeliveries_dedupe(
        tmp_path, monkeypatch):
    _state(tmp_path, monkeypatch)
    event = {"mode": "pr", "repo": "orders-api", "pr": 201,
             "key": "PROJ-301", "updated": "sha1"}
    first_code, first = receiver.handle_event(event)
    second_code, second = receiver.handle_event({
        key: value for key, value in event.items() if key != "key"})

    assert first_code == 200 and first["accepted"] is True
    assert second_code == 200 and second["accepted"] is False
    assert first["idempotency_key"] == second["idempotency_key"]
    queue = receiver.work_queue.load()
    assert len(queue) == 1
    assert queue[0]["ticket"] == "PROJ-301"
    assert queue[0]["requested_by"] == "taskevent"


def test_invalid_explicit_key_is_not_marked_seen_and_corrected_retry_succeeds(
        tmp_path, monkeypatch):
    _state(tmp_path, monkeypatch)
    event = {"mode": "pr", "repo": "orders-api", "pr": 201,
             "key": "ticket PROJ-301", "updated": "sha1"}
    code, body = receiver.handle_event(event)
    assert code == 400 and "one bare JIRA key" in body["error"]
    assert receiver.work_queue.load() == []
    assert not receiver.SEEN_FILE.exists()

    corrected = {**event, "key": "PROJ-301"}
    code, body = receiver.handle_event(corrected)
    assert code == 200 and body["accepted"] is True
    assert receiver.work_queue.load()[0]["ticket"] == "PROJ-301"


def test_non_string_pr_key_is_rejected_at_the_receiver_boundary(
        tmp_path, monkeypatch):
    _state(tmp_path, monkeypatch)
    code, body = receiver.handle_event({
        "mode": "pr", "repo": "orders-api", "pr": 201,
        "key": {"nested": "PROJ-301"}, "updated": "sha1"})
    assert code == 400 and body["error"] == "key must be a string"
    assert receiver.work_queue.load() == []
    assert not receiver.SEEN_FILE.exists()


def test_omitted_pr_key_preserves_the_original_queue_shape(tmp_path, monkeypatch):
    _state(tmp_path, monkeypatch)
    code, body = receiver.handle_event({
        "mode": "pr", "repo": "orders-api", "pr": 201, "updated": "sha1"})
    assert code == 200 and body["accepted"] is True
    assert "ticket" not in receiver.work_queue.load()[0]
