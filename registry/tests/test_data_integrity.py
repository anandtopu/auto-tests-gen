"""Torn-write protection for the shared JSON state stores.

The defect class: every store wrote DIRECTLY to its final path, so a crash mid-write
(OOM kill, pod eviction, power loss) left a truncated file. Loaders that swallowed
the parse error returned empty state — and the next save OVERWROTE real data
(including human plan approvals) with nothing. Loaders that did not swallow took the
review board, queue and wizard down until someone hand-edited the file.

These tests pin the fix at both layers: the atomic writer cannot tear, and a corrupt
file met anyway is quarantined — preserved and visible — never silently emptied.
"""
import json
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
import fs_lock


# ------------------------------------------------------------ the writer

def test_lock_release_retries_only_the_owner_marker(tmp_path, monkeypatch):
    """A transient Windows unlink failure must not leave a live-PID orphan,
    while the unsafe directory-removal retry remains forbidden."""
    lockdir = tmp_path / "state.lock"
    lockdir.mkdir()
    owner = lockdir / "owner"
    owner.write_text("123 1", encoding="utf-8")
    real_unlink = pathlib.Path.unlink
    calls = {"owner": 0}

    def transient_unlink(path, *args, **kwargs):
        if path == owner:
            calls["owner"] += 1
            if calls["owner"] == 1:
                raise PermissionError("simulated sharing violation")
        return real_unlink(path, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "unlink", transient_unlink)
    fs_lock._release(lockdir)

    assert calls["owner"] == 2
    assert not lockdir.exists()

def test_atomic_write_survives_a_crash_at_the_replace_boundary(tmp_path, monkeypatch):
    """The whole point: if the process dies at ANY instant, the file on disk is
    either the old complete document or the new complete document — never torn."""
    target = tmp_path / "state.json"
    fs_lock.write_json_atomic(target, {"generation": 1})
    assert json.loads(target.read_text(encoding="utf-8")) == {"generation": 1}

    import os as _os
    real_replace = _os.replace

    def dies(src, dst):
        raise OSError("simulated crash during replace")

    monkeypatch.setattr("os.replace", dies)
    with pytest.raises(OSError):
        fs_lock.write_json_atomic(target, {"generation": 2})
    monkeypatch.setattr("os.replace", real_replace)

    # The old document is intact — not truncated, not half-written...
    assert json.loads(target.read_text(encoding="utf-8")) == {"generation": 1}
    # ...and the failed attempt left no tmp litter for globs/bundles to sweep up.
    assert list(tmp_path.glob("*.tmp")) == [] and list(tmp_path.glob(".*.tmp")) == []


def test_atomic_write_creates_parents_and_roundtrips(tmp_path):
    target = tmp_path / "deep/nested/state.json"
    fs_lock.write_json_atomic(target, {"k": [1, 2, 3]}, sort_keys=True)
    assert json.loads(target.read_text(encoding="utf-8")) == {"k": [1, 2, 3]}


# ------------------------------------------------------------ the reader

def test_corrupt_state_is_quarantined_not_silently_emptied(tmp_path, capsys):
    """Returning {} while LEAVING the corrupt file in place is the silent-data-loss
    path: the next save overwrites real bytes with empty state. Quarantine keeps
    the bytes and makes the event visible."""
    target = tmp_path / "state.json"
    target.write_text('{"PROJ-1": {"status": "approv', encoding="utf-8")  # torn

    assert fs_lock.read_json_guarded(target, {}) == {}
    assert not target.exists(), "the corrupt file must be moved aside, not left"
    q = list(tmp_path.glob("state.json.corrupt-*"))
    assert len(q) == 1, "the damaged bytes must be preserved for recovery"
    assert "approv" in q[0].read_text(encoding="utf-8")
    assert "corrupt" in capsys.readouterr().err, "the event must be loud, not silent"

    # A fresh save now writes a NEW file and never touches the quarantine.
    fs_lock.write_json_atomic(target, {"fresh": True})
    assert json.loads(target.read_text(encoding="utf-8")) == {"fresh": True}
    assert len(list(tmp_path.glob("state.json.corrupt-*"))) == 1


def test_reader_defaults_for_missing_file(tmp_path):
    assert fs_lock.read_json_guarded(tmp_path / "absent.json", []) == []
    assert fs_lock.read_json_guarded(tmp_path / "absent.json", {"d": 1}) == {"d": 1}


# ---------------------------------------------- the stores actually use it

def test_plan_approvals_survive_a_torn_state_file(tmp_path, monkeypatch):
    """The worst historical case: plan_state swallowed the parse error, so a torn
    write followed by any mutation REPLACED every human approval with {}."""
    import plan_state
    monkeypatch.setattr(plan_state, "DIR", tmp_path)
    monkeypatch.setattr(plan_state, "FILE", tmp_path / "state.json")
    monkeypatch.setattr(plan_state, "PLAN_DIR", tmp_path)

    (tmp_path / "state.json").write_text('{"PROJ-9": {"status": "appr', encoding="utf-8")
    assert plan_state.load() == {}                       # continues from empty
    q = list(tmp_path.glob("state.json.corrupt-*"))
    assert q, "the approvals that were in the torn file are preserved on disk"

    # New writes go through the atomic path and do not disturb the quarantine.
    (tmp_path / "NEW-1.md").write_text("# plan", encoding="utf-8")
    plan_state.record_plan("NEW-1", {"scenarios": []})
    assert plan_state.get("NEW-1")["status"] == "draft"
    assert len(list(tmp_path.glob("state.json.corrupt-*"))) == 1


def test_review_board_and_queue_no_longer_crash_on_a_torn_file(tmp_path, monkeypatch):
    import review_state
    import work_queue
    monkeypatch.setattr(review_state, "FILE", tmp_path / "reviews.json")
    monkeypatch.setattr(work_queue, "FILE", tmp_path / "queue.json")

    (tmp_path / "reviews.json").write_text('{"PR-1": {"stat', encoding="utf-8")
    (tmp_path / "queue.json").write_text('[{"id": "q1"', encoding="utf-8")

    assert review_state.load() == {}      # used to raise -> board/wizard down
    assert work_queue.load() == []        # used to raise -> queue APIs down
    assert list(tmp_path.glob("reviews.json.corrupt-*"))
    assert list(tmp_path.glob("queue.json.corrupt-*"))


def test_every_shared_state_store_writes_atomically():
    """Source pin: nobody quietly reverts a store to a direct write. The receiver's
    dedupe window is included — losing it re-enqueues the sender's retries."""
    stores = ["engine/lib/plan_state.py", "engine/lib/review_state.py",
              "engine/lib/work_queue.py", "engine/lib/openhands_events.py",
              "engine/lib/test_health.py", "bin/taskevent_receiver.py"]
    for f in stores:
        src = (ROOT / f).read_text(encoding="utf-8")
        assert "write_json_atomic" in src, f"{f} must use the atomic writer"
        assert "read_json_guarded" in src, f"{f} must use the guarded reader"


def test_quarantine_files_never_enter_a_state_bundle(tmp_path, monkeypatch):
    """One deployment's damage must not be planted into another via export/import."""
    import state_bundle as sb
    src = tmp_path / "src"
    (src / "reports/runs").mkdir(parents=True)
    (src / "reports/runs/reviews.json").write_text("{}", encoding="utf-8")
    (src / "reports/runs/reviews.json.corrupt-20260729-120000").write_text(
        '{"torn', encoding="utf-8")
    monkeypatch.setattr(sb, "ROOT", src)
    names = [r.as_posix() for r in sb.collect()]
    assert "reports/runs/reviews.json" in names
    assert not [n for n in names if ".corrupt-" in n]


# ------------------------------- functional: approvals are not one click from gone

def test_queueing_a_plan_rerun_cannot_silently_destroy_an_approval(tmp_path, monkeypatch):
    """J5's contract is that `approved` is a human sign-off. Queueing a plan-only
    run for an approved key re-authors the plan and resets it to draft — so one
    click of "Author test plan" on the wrong key erased a review. The queue now
    refuses unless forced; a draft plan re-queues freely."""
    import plan_state
    import work_queue
    monkeypatch.setattr(plan_state, "DIR", tmp_path)
    monkeypatch.setattr(plan_state, "FILE", tmp_path / "state.json")
    monkeypatch.setattr(plan_state, "PLAN_DIR", tmp_path)
    monkeypatch.setattr(work_queue, "FILE", tmp_path / "queue.json")

    (tmp_path / "K-9.md").write_text("# plan", encoding="utf-8")
    plan_state.record_plan("K-9", {"scenarios": []})
    plan_state.set_status("K-9", "approved", "a-human")

    with pytest.raises(SystemExit, match="APPROVED"):
        work_queue.add("plan", "K-9")
    assert plan_state.get("K-9")["status"] == "approved", "the sign-off survives"

    item, fresh = work_queue.add("plan", "K-9", force=True)
    assert fresh and item["mode"] == "plan", "force is the deliberate override"
    work_queue.remove(item["id"])

    plan_state.set_status("K-9", "draft", "a-human")
    item, fresh = work_queue.add("plan", "K-9")
    assert fresh, "re-authoring a DRAFT plan is legitimate refinement"
