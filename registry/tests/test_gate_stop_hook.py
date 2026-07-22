"""Regression tests for the OpenHands Stop hook that runs the quality gate
(.openhands/hooks.json + .openhands/hooks/gate-check.sh) and for the gate's
check-only mode it depends on.

The invariant under test: the hook tells an agent its work would be rejected
BEFORE it declares the task done, without ever committing or pushing — the gate
remains the only component permitted to write."""
import json, os, pathlib, shutil, subprocess, sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
import demo_data, work_queue

BASH = work_queue.bash_exe()


def _rm_workspace():
    """shutil.rmtree(ignore_errors=True) SILENTLY fails on a git clone under Windows
    (read-only objects), leaving the repo behind and poisoning the next test. Reuse
    the product's read-only-aware removal."""
    p = ROOT / "workspace/tests"
    if p.exists():
        demo_data._rmtree(p)
HOOK = ROOT / ".openhands/hooks/gate-check.sh"
REPO = ROOT / "workspace/tests/e2e-api-tests-1"


def _sh(args, cwd=ROOT, env=None):
    return subprocess.run([BASH, *args], cwd=cwd, capture_output=True, text=True,
                          encoding="utf-8", errors="replace",
                          stdin=subprocess.DEVNULL, timeout=400,
                          env={**os.environ, **(env or {})})


@pytest.fixture
def workspace():
    """A freshly cloned writable test repo, like a run in flight."""
    _rm_workspace()
    _sh(["adapters/mock/scm.sh", "clone_rw", "e2e-api-tests-1",
         "workspace/tests/e2e-api-tests-1", "test/HOOKTEST-ai-qe"])
    yield REPO
    _rm_workspace()


def _git(repo, *args):
    # stdin=DEVNULL: without it Windows hands the child an invalid handle (WinError 6)
    return subprocess.run(["git", "-C", str(repo), *args], capture_output=True,
                          text=True, encoding="utf-8", errors="replace",
                          stdin=subprocess.DEVNULL).stdout.strip()


def _head(repo):
    return _git(repo, "rev-parse", "HEAD")


def _add_mapped_spec(repo, name="hookok"):
    (repo / "suites/orders" / f"{name}.spec.js").write_text(
        'const {test}=require("node:test");\ntest("ok",()=>{});\n', encoding="utf-8")
    with open(repo / "catalog/generated.jsonl", "a", encoding="utf-8") as fh:
        fh.write('{"file":"suites/orders/%s.spec.js",'
                 '"mapping":{"status":"confirmed"}}\n' % name)


# ------------------------------------------------------- gate check-only mode

def test_check_only_runs_the_checks_but_never_commits(workspace):
    before = _head(workspace)
    _add_mapped_spec(workspace)
    r = _sh([str(ROOT / "engine/gate/gate.sh"), "HOOK-1", "e2e-api-tests-1"],
            cwd=workspace, env={"AIQE_ROOT": str(ROOT), "AIQE_GATE_CHECK_ONLY": "1"})
    assert r.returncode == 0, r.stdout + r.stderr
    assert "GATE_STATUS=WOULD_COMMIT" in r.stdout
    assert "GATE_STATUS=COMMITTED" not in r.stdout
    assert _head(workspace) == before, "check-only must not commit"
    assert _git(workspace, "status", "--porcelain"), \
        "the work must still be sitting uncommitted"


def test_check_only_still_enforces_every_check(workspace):
    """Skipping the commit must not skip the checks."""
    (workspace / "suites/orders/unmapped.spec.js").write_text(
        'const {test}=require("node:test");\ntest("x",()=>{});\n', encoding="utf-8")
    r = _sh([str(ROOT / "engine/gate/gate.sh"), "HOOK-2", "e2e-api-tests-1"],
            cwd=workspace, env={"AIQE_ROOT": str(ROOT), "AIQE_GATE_CHECK_ONLY": "1"})
    assert r.returncode == 4, r.stdout        # born-mapped rule still fires
    assert "UNMAPPED_TEST" in r.stdout


def test_default_mode_still_commits(workspace):
    """Guard against the flag inverting: without it the gate must behave as before."""
    before = _head(workspace)
    _add_mapped_spec(workspace, "normal")
    r = _sh([str(ROOT / "engine/gate/gate.sh"), "HOOK-3", "e2e-api-tests-1"],
            cwd=workspace, env={"AIQE_ROOT": str(ROOT)})
    assert r.returncode == 0 and "GATE_STATUS=COMMITTED" in r.stdout
    assert _head(workspace) != before, "the real gate must still commit"


# ------------------------------------------------------------------ the hook

def test_hook_allows_when_the_gate_would_pass(workspace):
    _add_mapped_spec(workspace)
    r = _sh([str(HOOK)], env={"OPENHANDS_PROJECT_DIR": str(ROOT)})
    assert r.returncode == 0
    assert json.loads(r.stdout)["decision"] == "allow"


def test_hook_blocks_and_explains_when_the_gate_would_reject(workspace):
    (workspace / "suites/orders/orphan.spec.js").write_text(
        'const {test}=require("node:test");\ntest("x",()=>{});\n', encoding="utf-8")
    r = _sh([str(HOOK)], env={"OPENHANDS_PROJECT_DIR": str(ROOT)})
    assert r.returncode == 2, "exit 2 is what blocks completion in OpenHands"
    d = json.loads(r.stdout)
    assert d["decision"] == "deny"
    assert "born-mapped" in d["reason"]                 # the actual rule
    assert "orphan.spec.js" in d["reason"]              # the actual file
    assert "not permitted" in d["reason"] or "only component" in d["reason"]


def test_hook_does_not_commit_even_when_it_allows(workspace):
    _add_mapped_spec(workspace)
    before = _head(workspace)
    _sh([str(HOOK)], env={"OPENHANDS_PROJECT_DIR": str(ROOT)})
    assert _head(workspace) == before, "the hook must never write to the repo"


def test_hook_fails_open_when_there_is_nothing_to_check(tmp_path):
    """A hook that cannot determine an answer must not block the agent on its own
    malfunction — the real gate still runs later and will reject."""
    r = _sh([str(HOOK)], env={"OPENHANDS_PROJECT_DIR": str(tmp_path)})
    assert r.returncode == 0 and json.loads(r.stdout)["decision"] == "allow"


def test_hook_allows_when_no_work_is_in_flight():
    _rm_workspace()
    assert not (ROOT / "workspace/tests").exists(), "cleanup must actually remove it"
    r = _sh([str(HOOK)], env={"OPENHANDS_PROJECT_DIR": str(ROOT)})
    assert r.returncode == 0 and json.loads(r.stdout)["decision"] == "allow"


# ------------------------------------------------------------------ wiring

def test_hooks_json_registers_the_stop_event():
    cfg = json.loads((ROOT / ".openhands/hooks.json").read_text(encoding="utf-8"))
    assert "stop" in cfg, "must bind the blocking `stop` event"
    hooks = cfg["stop"][0]["hooks"]
    assert any(h["command"].endswith("gate-check.sh") for h in hooks)
    h = hooks[0]
    assert h.get("async") is False, "a blocking check cannot be async"
    assert h.get("timeout", 0) >= 300, "must outlast the gate's test execution"


def test_hook_never_pushes():
    """Belt and braces: the hook script itself must contain no write verbs."""
    src = HOOK.read_text(encoding="utf-8")
    body = src.split("set -uo pipefail", 1)[1]     # skip the explanatory header
    for verb in ("git push", "git commit", "git add"):
        assert verb not in body, f"stop hook must not {verb}"
