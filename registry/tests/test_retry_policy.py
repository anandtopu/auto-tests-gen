"""Retrying a failed request, bounded — and the bound says why.

A retry is a FULL pipeline run: clones, an LLM call per phase, possibly a
commit. `requeue` had no limit at all, so a stuck UI or an impatient loop could
spend real money re-running a request that fails identically every time. It also
cleared exit_code and finished, so the third attempt looked exactly like the
first — which lost the information a user needs AND made any limit
unenforceable.

The properties worth pinning are the ones that make a limiter trustworthy: it
counts something a user cannot trivially reset, it survives the process, and a
refusal names the limit and the wait instead of just saying no.
"""
import json
import pathlib
import sys
import time

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
import retry_policy as rp  # noqa: E402
import work_queue  # noqa: E402


@pytest.fixture
def estate(tmp_path, monkeypatch):
    (tmp_path / "reports").mkdir(parents=True, exist_ok=True)
    (tmp_path / "registry").mkdir(parents=True, exist_ok=True)
    (tmp_path / "registry/org-config.yaml").write_text(
        "retry:\n  max_attempts: 3\n  window_minutes: 60\n  cooldown_seconds: 60\n",
        encoding="utf-8")
    monkeypatch.setattr(rp, "FILE", tmp_path / "reports/retries.json")
    return tmp_path


def test_a_fresh_key_is_allowed_and_says_which_attempt_it_is(estate):
    v = rp.check("PR-x-1", root=estate)
    assert v["allowed"] and v["attempts"] == 0
    assert "attempt 1 of 3" in v["reason"]


def test_the_cooldown_refusal_names_the_wait(estate):
    rp.record("PR-x-1", root=estate)
    v = rp.check("PR-x-1", root=estate)
    assert not v["allowed"] and v["limit"] == "cooldown"
    assert v["retry_after_seconds"] > 0
    assert "Try again in" in v["reason"], "a refusal with no wait makes the user guess"


def test_the_attempt_cap_refuses_and_suggests_a_different_action(estate):
    """Three identical failures need a change, not a fourth attempt — the
    message should say so rather than just counting."""
    now = time.time()
    for i in range(3):
        rp.record("PR-x-1", root=estate, now=now - 600 * (3 - i))
    v = rp.check("PR-x-1", root=estate, now=now)
    assert not v["allowed"] and v["limit"] == "max_attempts"
    assert v["attempts"] == 3
    assert "needs a change, not another attempt" in v["reason"]
    assert v["retry_after_seconds"] > 0


def test_attempts_outside_the_window_no_longer_count(estate):
    now = time.time()
    for _ in range(3):
        rp.record("PR-x-1", root=estate, now=now - 3600 * 2)   # 2h ago, window 60m
    v = rp.check("PR-x-1", root=estate, now=now)
    assert v["allowed"], "an expired window must release the key"
    assert v["attempts"] == 0


def test_the_counter_is_per_key_not_per_queue_item(estate):
    """A queue item id changes when it is removed and re-added, so counting per
    item is a limit anyone can reset by clicking Remove."""
    rp.record("PR-x-1", root=estate)
    assert rp.check("PR-x-2", root=estate)["allowed"], "keys must not share a budget"
    assert not rp.check("PR-x-1", root=estate)["allowed"]


def test_the_record_survives_the_process(estate):
    rp.record("PR-x-1", root=estate)
    data = json.loads((estate / "reports/retries.json").read_text(encoding="utf-8"))
    assert "PR-x-1" in data and len(data["PR-x-1"]) == 1


def test_aged_out_keys_are_pruned_so_the_file_does_not_grow_forever(estate):
    now = time.time()
    rp.record("OLD-1", root=estate, now=now - 3600 * 5)
    rp.record("NEW-1", root=estate, now=now)
    data = json.loads((estate / "reports/retries.json").read_text(encoding="utf-8"))
    assert "NEW-1" in data and "OLD-1" not in data


def test_a_malformed_limit_falls_back_rather_than_becoming_zero(estate):
    """A zero here either blocks every retry or disables the limit; both are
    worse than the default."""
    (estate / "registry/org-config.yaml").write_text(
        "retry:\n  max_attempts: nonsense\n  cooldown_seconds: -5\n", encoding="utf-8")
    problems = []
    lim = rp.limits(root=estate, problems=problems)
    assert lim["max_attempts"] == rp.DEFAULTS["max_attempts"]
    assert lim["cooldown_seconds"] == rp.DEFAULTS["cooldown_seconds"]
    # And it SAYS which values it could not use. A typo that silently reverts a
    # limit is a limit nobody knows they lost.
    assert any("max_attempts" in p for p in problems), problems
    assert any("cooldown_seconds" in p for p in problems), problems


def test_one_bad_value_does_not_discard_the_whole_section(estate):
    """A single typo used to escape into a blanket except and revert all three
    limits at once."""
    (estate / "registry/org-config.yaml").write_text(
        "retry:\n  max_attempts: nope\n  window_minutes: 15\n", encoding="utf-8")
    lim = rp.limits(root=estate)
    assert lim["max_attempts"] == rp.DEFAULTS["max_attempts"], "bad key not defaulted"
    assert lim["window_minutes"] == 15, "a GOOD value was thrown away with the bad one"


def test_an_absent_retry_section_is_still_bounded(estate):
    (estate / "registry/org-config.yaml").write_text("review: {}\n", encoding="utf-8")
    assert rp.limits(root=estate) == rp.DEFAULTS, \
        "an estate that configures nothing must not become unlimited"


# ------------------------------------------------------- the queue integration

def _force_failed(item_id, **extra):
    items = work_queue.load()
    for i in items:
        if i["id"] == item_id:
            i.update(status="failed", **extra)
    work_queue.save(items)


def test_requeue_keeps_the_previous_failure(tmp_path, monkeypatch):
    """The third attempt used to look exactly like the first."""
    monkeypatch.setattr(work_queue, "FILE", tmp_path / "queue.json")
    monkeypatch.setattr(rp, "FILE", tmp_path / "retries.json")
    item, _ = work_queue.add("jira", "ZZRETRY-1")
    _force_failed(item["id"], exit_code=5, error="TESTS_FAILED at the gate")

    work_queue.requeue(item["id"])
    back = next(i for i in work_queue.load() if i["id"] == item["id"])
    assert back["status"] == "queued"
    assert back["attempts"] == 2, "the attempt count did not advance"
    assert back["last_error"] == "TESTS_FAILED at the gate", \
        "the previous failure was erased - the retry looks like a first try"
    assert back["last_exit_code"] == 5


def test_requeue_is_refused_once_the_limit_is_hit(tmp_path, monkeypatch):
    monkeypatch.setattr(work_queue, "FILE", tmp_path / "queue.json")
    monkeypatch.setattr(rp, "FILE", tmp_path / "retries.json")
    item, _ = work_queue.add("jira", "ZZRETRY-2")
    _force_failed(item["id"], exit_code=5, error="boom")
    work_queue.requeue(item["id"])          # first retry: allowed
    _force_failed(item["id"], exit_code=5, error="boom")
    with pytest.raises(SystemExit) as e:    # second, immediately: cooldown
        work_queue.requeue(item["id"])
    assert "RETRY_RATE_LIMITED" in str(e.value)
    assert "Try again in" in str(e.value)


def test_force_is_the_documented_override(tmp_path, monkeypatch):
    """An operator who knows better must have a way through - but deliberately,
    not by default."""
    monkeypatch.setattr(work_queue, "FILE", tmp_path / "queue.json")
    monkeypatch.setattr(rp, "FILE", tmp_path / "retries.json")
    item, _ = work_queue.add("jira", "ZZRETRY-3")
    _force_failed(item["id"], exit_code=5, error="boom")
    work_queue.requeue(item["id"])
    _force_failed(item["id"])
    work_queue.requeue(item["id"], force=True)      # must not raise
    back = next(i for i in work_queue.load() if i["id"] == item["id"])
    assert back["status"] == "queued"


def test_a_rate_limited_retry_answers_429_not_409():
    """409 invites a client to resolve a conflict and try again - which would
    hammer the limit that just refused it."""
    src = (ROOT / "bin/dashboard_server.py").read_text(encoding="utf-8")
    assert '429 if "RETRY_RATE_LIMITED" in msg else 409' in src
    block = src[src.index('elif self.path == "/api/runs/retry":'):]
    block = block[:block.index("elif self.path.startswith(")]
    assert "self._send(429" in block, "the run-retry path has no rate-limit response"
    assert "nothing to retry" in block, "retrying an unknown key must 404, not queue work"
    assert "running right now" in block, "a live run must not be retried underneath itself"
