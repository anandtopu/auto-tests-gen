"""Regression tests for the P1 features: state-file locking, run-record
retention, the TaskEvent receiver, and dashboard auth."""
import json, os, pathlib, socket, subprocess, sys, time, urllib.error, urllib.request

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
import fs_lock


# --- fs_lock ---------------------------------------------------------------------

def test_lock_acquire_release(tmp_path):
    target = tmp_path / "state.json"
    with fs_lock.lock(target):
        assert (tmp_path / "state.json.lock").is_dir()
    assert not (tmp_path / "state.json.lock").exists()


def test_lock_contention_times_out(tmp_path):
    target = tmp_path / "state.json"
    with fs_lock.lock(target):
        with pytest.raises(TimeoutError):
            with fs_lock.lock(target, timeout=0.3):
                pass


def test_lock_breaks_stale(tmp_path):
    target = tmp_path / "state.json"
    lockdir = tmp_path / "state.json.lock"
    lockdir.mkdir()
    (lockdir / "owner").write_text(f"999999 {time.time() - fs_lock.STALE_S - 5}")
    with fs_lock.lock(target, timeout=2):          # stale holder -> broken + acquired
        assert (lockdir / "owner").exists()


# --- run-record retention (qa.py prune) ------------------------------------------

def test_prune_keeps_newest(tmp_path):
    for i in range(5):
        rid = f"100{i}-1"
        (tmp_path / f"{rid}.json").write_text(json.dumps(
            {"run_id": rid, "ts": 1000 + i, "trigger": {"type": "pr", "key": "K"}}))
        (tmp_path / f"{rid}-e2e-api-tests-1.diff").write_text("diff")
    (tmp_path / "reviews.json").write_text("{}")   # state files are never pruned
    r = subprocess.run([sys.executable, str(ROOT / "bin/qa.py"), "prune",
                        "--keep", "2", "--dir", str(tmp_path)],
                       capture_output=True, text=True, cwd=ROOT, stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stderr
    left = sorted(p.name for p in tmp_path.glob("*.json"))
    assert left == ["1003-1.json", "1004-1.json", "reviews.json"]
    assert sorted(p.name for p in tmp_path.glob("*.diff")) == \
        ["1003-1-e2e-api-tests-1.diff", "1004-1-e2e-api-tests-1.diff"]


# --- TaskEvent receiver ----------------------------------------------------------

def _run_handler(events, tmp_path):
    script = (
        "import importlib.util, json, sys\n"
        f"spec = importlib.util.spec_from_file_location('ter', {str(ROOT / 'bin/taskevent_receiver.py')!r})\n"
        "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
        "print(json.dumps([m.handle_event(e) for e in json.loads(sys.argv[1])]))\n")
    env = {**os.environ, "AIQE_QUEUE_FILE": str(tmp_path / "queue.json"),
           "AIQE_HOOKS_SEEN": str(tmp_path / "seen.json")}
    r = subprocess.run([sys.executable, "-c", script, json.dumps(events)],
                       capture_output=True, text=True, cwd=ROOT, env=env,
                       stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_taskevent_validate_enqueue_dedupe(tmp_path):
    pr = {"mode": "pr", "repo": "orders-api", "pr": 201, "updated": "sha1"}
    results = _run_handler([
        {"mode": "nope"},                          # invalid mode
        {"mode": "pr", "repo": "orders-api"},      # missing pr number
        pr,                                        # accepted
        pr,                                        # duplicate delivery -> no-op
        {"mode": "jira", "key": "PROJ-301", "updated": "t1"},
    ], tmp_path)
    codes = [r[0] for r in results]
    assert codes == [400, 400, 200, 200, 200]
    assert results[2][1]["accepted"] is True
    assert results[3][1]["accepted"] is False and "duplicate" in results[3][1]["reason"]
    assert results[4][1]["accepted"] is True
    queue = json.load(open(tmp_path / "queue.json"))
    assert [(i["mode"], i["target"]) for i in queue] == \
        [("pr", "orders-api"), ("jira", "PROJ-301")]


# --- dashboard auth --------------------------------------------------------------

def test_dashboard_auth_token(tmp_path):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    env = {**os.environ, "AIQE_UI_TOKEN": "sekret", "AIQE_UI_PORT": str(port)}
    proc = subprocess.Popen([sys.executable, str(ROOT / "bin/dashboard_server.py")],
                            cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        base = f"http://127.0.0.1:{port}"
        for _ in range(50):                        # wait for the server to come up
            try:
                urllib.request.urlopen(f"{base}/api/queue", timeout=1)
                break
            except urllib.error.HTTPError:
                break                              # responding (401) = up
            except OSError:
                time.sleep(0.2)
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(f"{base}/api/queue", timeout=5)
        assert e.value.code == 401
        req = urllib.request.Request(f"{base}/api/queue",
                                     headers={"Authorization": "Bearer sekret"})
        assert urllib.request.urlopen(req, timeout=5).status == 200
        resp = urllib.request.urlopen(f"{base}/api/queue?token=sekret", timeout=5)
        assert resp.status == 200
        assert "aiqe_token=sekret" in (resp.headers.get("Set-Cookie") or "")
    finally:
        proc.terminate()
        proc.wait(timeout=10)
