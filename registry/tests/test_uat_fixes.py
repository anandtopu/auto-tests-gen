"""Pins for the fixes from the 2026-07-30 end-to-end UAT pass.

Findings fixed here:
  1. /api/repos/scope silently cleared a hand-managed scope when the `apps`
     field was missing (destructive default) -> the field is now required.
     (Handler-level; the repo_admin behavior itself is unchanged.)
  2. The review store accepted any key -> phantom rows on the team board.
     require_known() guards user-initiated transitions.
  3. Queue intake validated repos only on the pasted-URL path -> the plain
     name+number path (wizard form, API, TaskEvent webhook) now refuses an
     unregistered repo, and jira/plan/tests modes refuse an invalid key.
  4. unquarantine left `"quarantined": false` residue in tracked catalog
     JSONL -> the tag is popped, restoring the original bytes.
  5. Raw KeyError text leaked as API error messages -> _err() names the field.
  6. changes_requested without a note was accepted by the APIs while the UI
     required one -> both stores now refuse it.
"""
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))


# ---------------------------------------------------------------- finding 2
@pytest.fixture
def reviews(tmp_path, monkeypatch):
    import review_state as rs
    monkeypatch.setattr(rs, "FILE", tmp_path / "reviews.json")
    return rs


def test_unknown_key_is_refused_at_the_boundary(reviews, tmp_path, monkeypatch):
    rs = reviews
    monkeypatch.setattr(rs, "ROOT", tmp_path)      # no run records, no plans
    with pytest.raises(SystemExit):
        rs.require_known("GHOST-1")


def test_run_record_key_is_known(reviews, tmp_path, monkeypatch):
    rs = reviews
    monkeypatch.setattr(rs, "ROOT", tmp_path)
    runs = tmp_path / "reports/runs"
    runs.mkdir(parents=True)
    (runs / "123-1.json").write_text(
        json.dumps({"trigger": {"type": "pr", "key": "PR-a-9"}}), encoding="utf-8")
    rs.require_known("PR-a-9")                     # does not raise


def test_existing_review_entry_stays_transitionable(reviews, tmp_path, monkeypatch):
    rs = reviews
    monkeypatch.setattr(rs, "ROOT", tmp_path)
    rs.set_status("OLD-1", "pending_review", "seed")   # store-level write (pipeline)
    rs.require_known("OLD-1")                      # its runs may be pruned; entry counts


def test_torn_run_record_never_blocks_the_board(reviews, tmp_path, monkeypatch):
    rs = reviews
    monkeypatch.setattr(rs, "ROOT", tmp_path)
    runs = tmp_path / "reports/runs"
    runs.mkdir(parents=True)
    (runs / "torn.json").write_text("{not json", encoding="utf-8")
    (runs / "ok.json").write_text(
        json.dumps({"trigger": {"key": "PR-b-1"}}), encoding="utf-8")
    assert "PR-b-1" in rs.known_keys()


# ---------------------------------------------------------------- finding 6
def test_changes_requested_requires_a_note_in_both_stores(reviews, tmp_path,
                                                          monkeypatch):
    rs = reviews
    with pytest.raises(SystemExit):
        rs.set_status("K-1", "changes_requested", "qa", "")
    rs.set_status("K-1", "changes_requested", "qa", "add authz cases")  # with note: fine

    import plan_state as ps
    monkeypatch.setattr(ps, "DIR", tmp_path / "plans")
    monkeypatch.setattr(ps, "FILE", tmp_path / "plans/state.json")
    monkeypatch.setattr(ps, "PLAN_DIR", tmp_path / "testplans")
    (tmp_path / "plans").mkdir()
    (tmp_path / "testplans").mkdir()
    (tmp_path / "testplans/K-2.md").write_text("# plan\n", encoding="utf-8")
    ps.record_plan("K-2", {"scenarios": []})
    with pytest.raises(SystemExit):
        ps.set_status("K-2", "changes_requested", "qa", "")
    ps.set_status("K-2", "changes_requested", "qa", "cover the error path")


# ---------------------------------------------------------------- finding 3
@pytest.fixture
def queue(tmp_path, monkeypatch):
    import work_queue as wq
    monkeypatch.setattr(wq, "FILE", tmp_path / "queue.json")
    return wq


def test_pr_intake_refuses_an_unregistered_repo(queue):
    with pytest.raises(SystemExit) as e:
        queue.add("pr", "ghost-repo", "5")
    assert "not a registered repository" in str(e.value)


def test_pr_intake_accepts_a_registered_repo(queue):
    item, fresh = queue.add("pr", "orders-api", "42")
    assert fresh and item["target"] == "orders-api"


def test_jira_intake_refuses_a_garbage_key(queue):
    with pytest.raises(SystemExit) as e:
        queue.add("jira", "lol not a key!!")
    assert "not a valid ticket key" in str(e.value)


def test_jira_intake_accepts_real_and_adhoc_keys(queue):
    assert queue.add("jira", "PROJ-301")[1]
    assert queue.add("jira", "ADHOC-1785440000-ab12")[1]   # inline-ticket shape


# ---------------------------------------------------------------- finding 4
def test_unquarantine_restores_the_original_entry_bytes(tmp_path, monkeypatch):
    """The lift must POP the tag, not write `false` — residue in a tracked
    JSONL makes every quarantine cycle permanent git noise."""
    cat = tmp_path / "catalog"
    cat.mkdir()
    entry = {"test_id": "r::f.spec.js::t1", "test_repo": "r", "file": "f.spec.js",
             "title": "t1", "mapping": {"app_repos": ["a"], "status": "auto"}}
    original = json.dumps(entry)
    (cat / "r.jsonl").write_text(original + "\n", encoding="utf-8")

    sys.path.insert(0, str(ROOT / "bin"))
    import types
    import qa
    monkeypatch.setattr(qa, "ROOT", tmp_path)      # load_catalog globs qa.ROOT/catalog

    qa.cmd_quarantine(types.SimpleNamespace(test_id="r::f.spec.js::t1",
                                            note="flaky", lift=False))
    tagged = json.loads((cat / "r.jsonl").read_text(encoding="utf-8"))
    assert tagged["mapping"]["quarantined"] is True
    assert tagged["mapping"]["quarantine_note"] == "flaky"

    qa.cmd_quarantine(types.SimpleNamespace(test_id="r::f.spec.js::t1",
                                            note="", lift=True))
    lifted = json.loads((cat / "r.jsonl").read_text(encoding="utf-8"))
    assert "quarantined" not in lifted["mapping"]
    assert "quarantine_note" not in lifted["mapping"]
    assert json.dumps(lifted) == original, "lift restores the original entry"


# ---------------------------------------------------------------- finding 5
def test_keyerror_renders_as_missing_field():
    sys.path.insert(0, str(ROOT / "bin"))
    import dashboard_server as ds
    assert ds._err(KeyError("target")) == "missing field: target"
    assert ds._err(ValueError("boom")) == "boom"
