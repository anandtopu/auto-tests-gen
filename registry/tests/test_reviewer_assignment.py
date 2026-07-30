"""Reviewer assignment (roadmap 1.5) — nudges toward a recorded acceptance rate.

Pins: no rota => no assignment (feature fully off); assignment is stable by key
hash (a re-committed key returns to the reviewer with context); assignment never
touches `reviewer`, which records who actually acted; the board and digest carry
the assignee.
"""
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
import review_state as rs


def test_no_rota_means_no_assignment(tmp_path, monkeypatch):
    monkeypatch.setattr(rs, "FILE", tmp_path / "r.json")
    monkeypatch.setattr(rs, "reviewers", lambda: [])
    assert rs._assignee_for("PR-x-1") == ""


def test_assignment_is_stable_and_spread(monkeypatch, tmp_path):
    monkeypatch.setattr(rs, "FILE", tmp_path / "r.json")
    monkeypatch.setattr(rs, "reviewers", lambda: ["alice", "bob", "carol"])
    keys = [f"PR-repo-{n}" for n in range(30)]
    picks = {k: rs._assignee_for(k) for k in keys}
    # Stable: same key, same assignee, every time.
    assert all(rs._assignee_for(k) == v for k, v in picks.items())
    # Spread: with 30 keys over 3 reviewers, everyone gets some.
    assert set(picks.values()) == {"alice", "bob", "carol"}


def test_assignment_is_a_nudge_not_an_actor_record(tmp_path, monkeypatch):
    monkeypatch.setattr(rs, "FILE", tmp_path / "r.json")
    e = rs.assign("PR-a-1", "alice")
    assert e["assigned_to"] == "alice"
    assert e.get("reviewer") is None, "assignment must not fabricate an actor"
    done = rs.set_status("PR-a-1", "approved", reviewer="bob")
    assert done["reviewer"] == "bob", "the decision records who actually acted"
    assert done["assigned_to"] == "alice", "and assignment history is kept"


def test_board_and_digest_carry_the_assignee():
    src = (ROOT / "bin/qa.py").read_text(encoding="utf-8")
    assert "assigned_to" in src, "the reviews board must show the assignee"
    dig = (ROOT / "engine/lib/email_notify.py").read_text(encoding="utf-8")
    assert "assigned_to" in dig, "the digest must show who was asked"
