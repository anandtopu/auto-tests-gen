"""Regression tests for the manual work queue (engine/lib/work_queue.py) and
the tracker search_release verb feeding the dashboard's fetch-by-release."""
import json, os, pathlib, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
WQ = str(ROOT / "engine/lib/work_queue.py")


def run(args, env_extra=None):
    env = {**os.environ, **(env_extra or {})}
    return subprocess.run([sys.executable, *args], capture_output=True, text=True,
                          cwd=ROOT, env=env, stdin=subprocess.DEVNULL)


def test_add_list_and_dedupe(tmp_path):
    env = {"AIQE_QUEUE_FILE": str(tmp_path / "queue.json")}
    r = run([WQ, "add", "jira", "PROJ-1", "", "2026.08", "tester"], env)
    assert r.returncode == 0 and "queued: PROJ-1" in r.stdout
    # duplicate while pending is a no-op
    r = run([WQ, "add", "jira", "PROJ-1"], env)
    assert "already queued" in r.stdout
    r = run([WQ, "add", "pr", "orders-api", "201", "2026.09"], env)
    assert "queued: PR-orders-api-201" in r.stdout
    items = json.load(open(tmp_path / "queue.json"))
    assert len(items) == 2 and items[1]["pr"] == "201"
    r = run([WQ, "list"], env)
    assert "PROJ-1" in r.stdout and "PR-orders-api-201" in r.stdout


def test_pr_mode_requires_number(tmp_path):
    env = {"AIQE_QUEUE_FILE": str(tmp_path / "queue.json")}
    r = run([WQ, "add", "pr", "orders-api"], env)
    assert r.returncode != 0 and "PR number" in (r.stdout + r.stderr)
    r = run([WQ, "add", "release", "x"], env)
    assert r.returncode != 0


sys.path.insert(0, str(ROOT / "engine/lib"))
import work_queue


def test_search_release_mock_adapter():
    # must use bash_exe(): plain "bash" from a Python subprocess resolves to
    # WSL's System32 stub outside Git Bash (the exact bug bash_exe() fixes)
    bash = work_queue.bash_exe()
    r = subprocess.run([bash, "adapters/mock/tracker.sh", "search_release", "2026.08"],
                       capture_output=True, text=True, cwd=ROOT, stdin=subprocess.DEVNULL)
    tickets = json.loads(r.stdout.strip().splitlines()[-1])
    assert any(t["key"] == "PROJ-301" for t in tickets)
    r = subprocess.run([bash, "adapters/mock/tracker.sh", "search_release", "1999.01"],
                       capture_output=True, text=True, cwd=ROOT, stdin=subprocess.DEVNULL)
    assert json.loads(r.stdout.strip().splitlines()[-1]) == []


def test_bash_exe_never_wsl():
    assert "system32" not in work_queue.bash_exe().lower()
