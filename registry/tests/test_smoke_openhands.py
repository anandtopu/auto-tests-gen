"""Regression tests for the OpenHands smoke-test script (bin/smoke-openhands.sh):
must fail loudly without credentials and pass plumbing checks in dry mode."""
import os, pathlib, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
import work_queue

FAKE = {"OPENHANDS_URL": "https://openhands.example.invalid",
        "OPENHANDS_API_KEY": "x", "ANTHROPIC_API_KEY": "x",
        "ATLASSIAN_MCP_TOKEN": "x", "GITHUB_TOKEN": "x", "SCM_KIND": "github"}


def run(env_extra, *args):
    env = {k: v for k, v in os.environ.items()
           if k not in FAKE and not k.startswith(("AIQE_SMOKE", "OPENHANDS"))}
    env.update(env_extra)
    return subprocess.run([work_queue.bash_exe(), "bin/smoke-openhands.sh", *args],
                          capture_output=True, text=True, cwd=ROOT,
                          stdin=subprocess.DEVNULL, env=env)


def test_missing_credentials_fail_loudly():
    r = run({})
    assert r.returncode == 1
    assert "OPENHANDS_URL MISSING" in r.stdout
    assert "ANTHROPIC_API_KEY MISSING" in r.stdout
    assert "summary:" in r.stdout and "failed" in r.stdout


def test_dry_run_with_credentials_passes():
    r = run(FAKE, "--dry")
    assert r.returncode == 0, r.stdout + r.stderr
    for marker in ("Stage 1", "Stage 5", "Stage 8", "OPENHANDS_URL set",
                   "TaskEvent schema parses", "0 failed"):
        assert marker in r.stdout, f"missing: {marker}\n{r.stdout}"
    # the expensive live-trigger stage is opt-in, never run implicitly
    assert "AIQE_SMOKE_TRIGGER=1" in r.stdout


def test_stash_kind_checks_stash_credentials():
    env = {**FAKE, "SCM_KIND": "stash"}
    env.pop("GITHUB_TOKEN")
    r = run(env, "--dry")
    assert "STASH_URL MISSING" in r.stdout and r.returncode == 1
