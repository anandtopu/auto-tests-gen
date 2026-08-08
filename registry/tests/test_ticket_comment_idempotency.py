"""JCTS-S5: safe, receipt-driven Jira comment idempotency."""
import json
import os
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))

import plan_state
import ticket_comment
import work_queue


def _completed(code=0, stdout="", stderr=""):
    return subprocess.CompletedProcess([], code, stdout=stdout, stderr=stderr)


def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(ticket_comment, "ROOT", tmp_path)
    monkeypatch.setattr(ticket_comment, "ATTEMPTS", tmp_path / "out/attempts.jsonl")
    monkeypatch.setenv("AIQE_EVENTS_DIR", str(tmp_path / "events"))
    monkeypatch.setenv("AIQE_JIRA_PLATFORM_ACCOUNT", "acct-aiqe")
    monkeypatch.setattr(ticket_comment.settings_store, "load_env_into", lambda: None)


def test_marker_is_visible_stable_and_run_is_not_part_of_the_digest():
    first, marker, first_hash = ticket_comment.decorate_body(
        "delivery", "PROJ-510", "AI-QE run run-a for PROJ-510", "run-a")
    retry, retry_marker, retry_hash = ticket_comment.decorate_body(
        "delivery", "PROJ-510", "AI-QE run run-b for PROJ-510", "run-b")
    assert marker == retry_marker == "aiqe:delivery:PROJ-510"
    assert first_hash == retry_hash
    assert first.endswith("⚙ aiqe:delivery:PROJ-510 · run run-a")
    assert retry.endswith("⚙ aiqe:delivery:PROJ-510 · run run-b")


def test_marker_is_inside_the_org_bound_and_truncation_is_explicit():
    body, marker, digest = ticket_comment.decorate_body(
        "delivery", "PROJ-510", "line\n" * 200, "run-a", max_chars=256)
    assert len(body) <= 256
    assert "comment content truncated" in body
    assert body.endswith("⚙ aiqe:delivery:PROJ-510 · run run-a")
    assert marker == "aiqe:delivery:PROJ-510" and len(digest) == 64


def test_retry_skips_unchanged_body_without_an_adapter_call(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    calls = []

    def adapter(_chosen, verb, *args, **_kwargs):
        calls.append((verb, args))
        return _completed(stdout="comment_id=c-1\n")

    monkeypatch.setattr(ticket_comment, "_adapter_run", adapter)
    monkeypatch.setenv("RUN_ID", "run-a")
    first = ticket_comment.post(
        "delivery", "PROJ-510", "AI-QE run run-a for PROJ-510", adapter="fake")
    monkeypatch.setenv("RUN_ID", "run-b")
    retry = ticket_comment.post(
        "delivery", "PROJ-510", "AI-QE run run-b for PROJ-510", adapter="fake")

    assert first["outcome"] == "posted"
    assert retry["outcome"] == "skipped_unchanged"
    assert retry["comment_id"] == "c-1"
    assert [verb for verb, _ in calls] == ["comment"]


def test_changed_body_updates_only_after_capability_and_author_guard(
        tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    calls = []

    def adapter(_chosen, verb, *args, **_kwargs):
        calls.append((verb, args))
        if verb == "comment_capabilities":
            return _completed(stdout="update_comment=available\n")
        return _completed(stdout="comment_id=c-1\n")

    monkeypatch.setattr(ticket_comment, "_adapter_run", adapter)
    monkeypatch.setenv("RUN_ID", "run-a")
    ticket_comment.post("plan", "PROJ-511", "first plan", adapter="fake")
    monkeypatch.setenv("RUN_ID", "run-b")
    updated = ticket_comment.post("plan", "PROJ-511", "changed plan", adapter="fake")

    assert updated["outcome"] == "updated" and updated["comment_id"] == "c-1"
    assert [verb for verb, _ in calls] == [
        "comment", "comment_capabilities", "update_comment"]
    update_args = calls[-1][1]
    assert update_args[0:2] == ("PROJ-511", "c-1")
    assert update_args[-1] == "acct-aiqe"
    assert "⚙ aiqe:plan:PROJ-511 · run run-b" in update_args[2]


def test_forged_author_uses_stated_supersession_and_records_reason(
        tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    calls = []

    def adapter(_chosen, verb, *args, **_kwargs):
        calls.append((verb, args))
        if verb == "comment_capabilities":
            return _completed(stdout="update_comment=available\n")
        if verb == "update_comment":
            return _completed(68, stderr="author_mismatch\n")
        return _completed(stdout=("comment_id=c-1\n" if len(calls) == 1
                                  else "comment_id=c-2\n"))

    monkeypatch.setattr(ticket_comment, "_adapter_run", adapter)
    ticket_comment.post("delivery", "PROJ-512", "old", adapter="fake")
    item = ticket_comment.post("delivery", "PROJ-512", "new", adapter="fake")

    assert item["outcome"] == "posted" and item["comment_id"] == "c-2"
    assert item["fallback_reason"] == "author_mismatch"
    assert item["supersedes_comment_id"] == "c-1"
    fallback_body = calls[-1][1][1]
    assert "Supersedes prior AI-QE comment c-1" in fallback_body
    assert "not authored by this platform account" in fallback_body


def test_ambiguous_update_failure_is_not_followed_by_duplicate_append(
        tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    calls = []

    def adapter(_chosen, verb, *_args, **_kwargs):
        calls.append(verb)
        if verb == "comment_capabilities":
            return _completed(stdout="update_comment=available\n")
        if verb == "update_comment":
            return _completed(1, stderr="connection reset after request")
        return _completed(stdout="comment_id=c-1\n")

    monkeypatch.setattr(ticket_comment, "_adapter_run", adapter)
    ticket_comment.post("delivery", "PROJ-513", "old", adapter="fake")
    item = ticket_comment.post("delivery", "PROJ-513", "new", adapter="fake")
    assert item["outcome"] == "failed"
    assert calls == ["comment", "comment_capabilities", "update_comment"]


def test_clarification_and_progress_kinds_remain_append_only(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    calls = []
    monkeypatch.setattr(
        ticket_comment, "_adapter_run",
        lambda _chosen, verb, *_args, **_kwargs:
        calls.append(verb) or _completed(stdout=f"comment_id=c-{len(calls)}\n"))
    for kind in sorted(ticket_comment.APPEND_ONLY_KINDS):
        ticket_comment.post(kind, "PROJ-514", "same question", adapter="fake")
        ticket_comment.post(kind, "PROJ-514", "same question", adapter="fake")
    assert calls == ["comment"] * (2 * len(ticket_comment.APPEND_ONLY_KINDS))


def test_prior_delivery_id_and_hash_are_found_in_run_history(tmp_path, monkeypatch):
    _isolate(tmp_path, monkeypatch)
    _, marker, digest = ticket_comment.decorate_body(
        "delivery", "PROJ-515", "AI-QE run run-old for PROJ-515", "run-old")
    run = tmp_path / "reports/runs/old.json"
    run.parent.mkdir(parents=True)
    run.write_text(json.dumps({"comments": [ticket_comment.receipt(
        "delivery", "PROJ-515", "posted", comment_id="history-1",
        run_id="run-old", ts=10, body_sha256=digest, marker=marker)]}),
        encoding="utf-8")
    monkeypatch.setenv("RUN_ID", "run-new")
    monkeypatch.setattr(ticket_comment, "_adapter_run",
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(
                            AssertionError("unchanged history must not call adapter")))
    item = ticket_comment.post(
        "delivery", "PROJ-515", "AI-QE run run-new for PROJ-515", adapter="fake")
    assert item["outcome"] == "skipped_unchanged"
    assert item["comment_id"] == "history-1"


def test_plan_state_preserves_idempotency_fields(tmp_path, monkeypatch):
    monkeypatch.setattr(plan_state, "DIR", tmp_path / "plans")
    monkeypatch.setattr(plan_state, "FILE", tmp_path / "plans/state.json")
    marker = ticket_comment.comment_marker("plan", "PROJ-516")
    digest = "a" * 64
    stored = plan_state.record_comment_attempt("PROJ-516", ticket_comment.receipt(
        "plan", "PROJ-516", "updated", comment_id="p-1", run_id="r",
        ts=1, body_sha256=digest, marker=marker))
    assert stored["comment_id"] == "p-1"
    assert stored["body_sha256"] == digest and stored["marker"] == marker


def _jira(tmp_path, verb, *args, account="acct-aiqe"):
    stub = tmp_path / "bin"
    stub.mkdir(exist_ok=True)
    calls = tmp_path / "curl-calls.log"
    curl = stub / "curl"
    curl.write_text(
        "#!/usr/bin/env bash\n"
        f"printf '%s\\n' \"$*\" >> '{calls.as_posix()}'\n"
        "case \" $* \" in\n"
        "  *\" -X PUT \"*) echo '{\"id\":\"4321\"}' ;;\n"
        "  *) echo '{\"id\":\"4321\",\"author\":{\"accountId\":\"acct-aiqe\"}}' ;;\n"
        "esac\n", encoding="utf-8")
    os.chmod(curl, 0o755)
    command, env = work_queue.git_bash_command(
        ROOT / "adapters/tracker/jira.sh", verb, *args, prepend=[stub],
        JIRA_URL="https://jira.example.com",
        ATLASSIAN_MCP_TOKEN="synthetic-token",
        AIQE_JIRA_PLATFORM_ACCOUNT=account)
    result = subprocess.run(
        command, cwd=ROOT, env=env, capture_output=True, text=True,
        encoding="utf-8", errors="replace", stdin=subprocess.DEVNULL,
        timeout=60, check=False)
    return result, calls


def test_jira_adapter_capability_and_owned_update(tmp_path):
    capability, _ = _jira(tmp_path, "comment_capabilities")
    assert capability.returncode == 0
    assert capability.stdout.strip() == "update_comment=available"
    updated, calls = _jira(
        tmp_path, "update_comment", "PROJ-517", "4321", "replacement",
        "acct-aiqe")
    assert updated.returncode == 0, updated.stderr
    assert updated.stdout.strip() == "comment_id=4321"
    assert "-X PUT" in calls.read_text(encoding="utf-8")


def test_jira_adapter_rejects_forged_marker_author_before_put(tmp_path):
    result, calls = _jira(
        tmp_path, "update_comment", "PROJ-518", "4321", "replacement",
        "human-account")
    assert result.returncode == 68
    assert result.stderr.strip() == "author_mismatch"
    assert "-X PUT" not in calls.read_text(encoding="utf-8")


def test_jira_update_path_honors_the_shared_tls_policy():
    source = (ROOT / "adapters/tracker/jira.sh").read_text(encoding="utf-8")
    update = source[source.index("  update_comment)"):]
    assert "UPDATE_FLAGS=(-s)" in update
    assert 'AIQE_SSL_VERIFY:-1' in update and 'UPDATE_FLAGS+=( -k)' not in update
    assert 'UPDATE_FLAGS+=("-k")' in update or 'UPDATE_FLAGS+=(-k)' in update
    assert 'curl "${UPDATE_FLAGS[@]}"' in update


def test_actual_mock_adapter_updates_only_its_own_comment(tmp_path):
    state = tmp_path / "comments.jsonl"
    env_args = {"AIQE_MOCK_TRACKER_COMMENTS": str(state)}
    command, env = work_queue.git_bash_command(
        ROOT / "adapters/mock/tracker.sh", "comment", "PROJ-519", "first",
        **env_args)
    first = subprocess.run(command, cwd=ROOT, env=env, capture_output=True,
                           text=True, encoding="utf-8", errors="replace",
                           stdin=subprocess.DEVNULL, check=False)
    comment_id = first.stdout.rsplit("comment_id=", 1)[-1].strip()
    command, env = work_queue.git_bash_command(
        ROOT / "adapters/mock/tracker.sh", "update_comment", "PROJ-519",
        comment_id, "second", "mock-platform", **env_args)
    updated = subprocess.run(command, cwd=ROOT, env=env, capture_output=True,
                             text=True, encoding="utf-8", errors="replace",
                             stdin=subprocess.DEVNULL, check=False)
    assert updated.returncode == 0 and f"comment_id={comment_id}" in updated.stdout
    rows = [json.loads(line) for line in state.read_text(encoding="utf-8").splitlines()]
    assert [row["op"] for row in rows] == ["post", "update"]


def test_full_mock_retry_updates_delivery_without_duplicate_repost(tmp_path):
    """M2 exit proof through the supported Jira pipeline entry point.

    The deterministic gate produces a new commit SHA on the retry, so the
    truthful delivery body changes and must be updated in place, not skipped.
    The unit-level unchanged case above separately proves A3.3.
    """
    log = ROOT / "out/mock-comments.log"
    old_log = log.read_text(encoding="utf-8", errors="replace") if log.exists() else ""
    before = {path.name for path in (ROOT / "reports/runs").glob("*.json")}
    env = {
        **os.environ,
        "AIQE_MOCK": "1",
        "AIQE_TICKET_COMMENTS_RICH": "1",
        "AIQE_PHASE_CACHE": "0",
        "AIQE_GENERATE_FANOUT": "0",
        "AIQE_MOCK_TRACKER_COMMENTS": str(tmp_path / "tracker-comments.jsonl"),
        "AIQE_PLAN_DIR": str(tmp_path / "plans"),
        "AIQE_TESTPLAN_DIR": str(tmp_path / "testplans"),
        "AIQE_SPEC_DIR": str(tmp_path / "specs"),
        "AIQE_TESTDATA_DIR": str(tmp_path / "testdata"),
    }
    for _ in range(2):
        result = subprocess.run(
            [work_queue.bash_exe(), "engine/pipeline.sh", "jira", "PROJ-301"],
            cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8",
            errors="replace", stdin=subprocess.DEVNULL, timeout=600, check=False)
        assert result.returncode == 0, result.stdout[-2000:] + result.stderr[-1000:]

    added_log = log.read_text(encoding="utf-8", errors="replace")[len(old_log):]
    assert added_log.count("[mock-jira] PROJ-301 <-") == 1
    created = [path for path in (ROOT / "reports/runs").glob("*.json")
               if path.name not in before]
    assert len(created) >= 2
    newest = json.loads(max(created, key=lambda path: path.stat().st_mtime)
                        .read_text(encoding="utf-8"))
    delivery = [row for row in newest["comments"]
                if row["kind"] == "delivery" and row["target"] == "PROJ-301"]
    assert delivery and delivery[-1]["outcome"] == "updated"
    mock_rows = [json.loads(line) for line in
                 (tmp_path / "tracker-comments.jsonl").read_text(
                     encoding="utf-8").splitlines()]
    assert [row["op"] for row in mock_rows] == ["post", "update"]
