"""Regression tests for team-review tracking (engine/lib/review_state.py + qa.py)."""
import json, os, pathlib, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
RS = str(ROOT / "engine/lib/review_state.py")


def run(args, env_extra=None, cwd=None):
    env = {**os.environ, **(env_extra or {})}
    return subprocess.run([sys.executable, *args], capture_output=True, text=True,
                          cwd=cwd or ROOT, env=env, stdin=subprocess.DEVNULL)


def test_set_get_and_history(tmp_path):
    env = {"AIQE_REVIEWS_FILE": str(tmp_path / "reviews.json")}
    r = run([RS, "set", "PROJ-9", "pending_review", "pipeline", "committed"], env)
    assert r.returncode == 0 and "pending_review" in r.stdout
    r = run([RS, "set", "PROJ-9", "approved", "alice", "LGTM"], env)
    assert r.returncode == 0
    data = json.load(open(tmp_path / "reviews.json"))
    e = data["PROJ-9"]
    assert e["status"] == "approved" and e["reviewer"] == "alice"
    assert [h["status"] for h in e["history"]] == ["pending_review", "approved"]


def test_invalid_status_rejected(tmp_path):
    env = {"AIQE_REVIEWS_FILE": str(tmp_path / "reviews.json")}
    r = run([RS, "set", "PROJ-9", "shipped"], env)
    assert r.returncode != 0 and "invalid status" in (r.stdout + r.stderr)


def test_auto_marks_committed_and_resets_approval(tmp_path):
    env = {"AIQE_REVIEWS_FILE": str(tmp_path / "reviews.json")}
    work = tmp_path / "work"; (work / "out").mkdir(parents=True)
    tsv = work / "out/gate_results.tsv"

    # no commits -> untouched
    tsv.write_text("e2e-api-tests-1\tno_changes\t0\t\n")
    r = run([RS, "auto", "PR-x-1"], env, cwd=work)
    assert "unchanged" in r.stdout

    # committed -> pending_review
    tsv.write_text("e2e-api-tests-1\tcommitted\t0\tabc123\n")
    r = run([RS, "auto", "PR-x-1"], env, cwd=work)
    assert "pending_review" in r.stdout

    # approval then a NEW commit -> back to pending_review (fresh artifacts)
    run([RS, "set", "PR-x-1", "approved", "bob"], env)
    r = run([RS, "auto", "PR-x-1"], env, cwd=work)
    assert "pending_review" in r.stdout
    data = json.load(open(tmp_path / "reviews.json"))
    assert "resets previous status: approved" in data["PR-x-1"]["note"]

    # already pending -> auto is a no-op
    r = run([RS, "auto", "PR-x-1"], env, cwd=work)
    assert "unchanged" in r.stdout


def test_release_tracking(tmp_path):
    env = {"AIQE_REVIEWS_FILE": str(tmp_path / "reviews.json")}
    # release can arrive before any status (ticket resolved before commit)
    r = run([RS, "release", "PROJ-7", "2026.08", "jira"], env)
    assert r.returncode == 0 and "2026.08" in r.stdout
    # idempotent on same value (no duplicate history)
    run([RS, "release", "PROJ-7", "2026.08", "jira"], env)
    data = json.load(open(tmp_path / "reviews.json"))
    assert data["PROJ-7"]["release"] == "2026.08"
    assert len([h for h in data["PROJ-7"]["history"] if "release" in h]) == 1
    # status transitions preserve the release
    run([RS, "set", "PROJ-7", "pending_review", "pipeline"], env)
    run([RS, "set", "PROJ-7", "approved", "alice"], env)
    data = json.load(open(tmp_path / "reviews.json"))
    assert data["PROJ-7"]["release"] == "2026.08"
    assert data["PROJ-7"]["status"] == "approved"


def test_qa_release_cli_and_board(tmp_path):
    env = {"AIQE_REVIEWS_FILE": str(tmp_path / "reviews.json")}
    r = run([str(ROOT / "bin/qa.py"), "release", "PR-orders-api-9", "2026.09"], env)
    assert r.returncode == 0 and "2026.09" in r.stdout
    r = run([str(ROOT / "bin/qa.py"), "reviews"], env)
    assert r.returncode == 0 and "2026.09" in r.stdout


def test_qa_cli_reviews_and_mark(tmp_path):
    env = {"AIQE_REVIEWS_FILE": str(tmp_path / "reviews.json")}
    r = run([str(ROOT / "bin/qa.py"), "mark", "PROJ-42", "in_review",
             "--by", "carol", "--note", "checking data cases"], env)
    assert r.returncode == 0 and "in_review" in r.stdout
    r = run([str(ROOT / "bin/qa.py"), "reviews"], env)
    assert r.returncode == 0 and "PROJ-42" in r.stdout and "carol" in r.stdout
    # status board still runs clean with the isolated review file
    assert run([str(ROOT / "bin/qa.py"), "status"], env).returncode == 0
