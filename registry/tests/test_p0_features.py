"""Regression tests for the P0 features: PR diff context, inline JIRA input,
issue-type-aware guidance, and the new Scm verbs."""
import json, os, pathlib, subprocess, sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
import inline_ticket, work_queue

BASH = work_queue.bash_exe()


def sh(args):
    return subprocess.run(args, capture_output=True, text=True, cwd=ROOT,
                          stdin=subprocess.DEVNULL)


# --- PR diff (P0 item 1) ---------------------------------------------------------

def test_mock_diff_returns_fixture_hunks():
    r = sh([BASH, "adapters/mock/scm.sh", "diff", "orders-api", "201"])
    assert r.returncode == 0
    assert "+++ b/openapi/orders.yaml" in r.stdout
    assert "+                pct: { type: number, minimum: 1, maximum: 90 }" in r.stdout
    # unknown PR -> empty, not an error (pipeline tolerates missing diffs)
    r = sh([BASH, "adapters/mock/scm.sh", "diff", "orders-api", "999"])
    assert r.returncode == 0 and r.stdout.strip() == ""


# --- build status (P0 item 5) ----------------------------------------------------

def test_mock_set_status():
    r = sh([BASH, "adapters/mock/scm.sh", "set_status", "orders-api", "abc123",
            "success", "AI-QE run 1"])
    assert r.returncode == 0 and "build status orders-api@abc123 -> success" in r.stdout


# --- inline JIRA context (P0 item 2) ---------------------------------------------

def test_inline_ticket_build():
    text = ("Discount hardening\n\nAs a shopper I cannot apply invalid discounts.\n"
            "AC-1: 1-90% accepted\n- AC-2: out-of-range rejected with 400\n")
    t = inline_ticket.build(text, components=["Checkout"], labels=["api-only"],
                            repos=["orders-api"], issue_type="Bug")
    assert t["summary"] == "Discount hardening"
    assert t["acceptance_criteria"] == ["AC-1: 1-90% accepted",
                                        "AC-2: out-of-range rejected with 400"]
    assert t["key"].startswith("ADHOC-") and t["inline"] is True
    assert t["issue_type"] == "Bug" and t["linked_repos"] == ["orders-api"]


def test_inline_ticket_rejects_empty():
    try:
        inline_ticket.build("   ")
        assert False
    except ValueError:
        pass


def test_inline_ticket_rejects_unsafe_key():
    # the key becomes a filename and a pipeline arg — reject injection/path chars
    for bad in ("XSS-<img src=x>", "a/b", "key with space", "a" * 65, "$(x)"):
        try:
            inline_ticket.build("summary\nAC-1: x", key=bad)
            assert False, f"accepted unsafe key: {bad!r}"
        except ValueError:
            pass
    # a normal ticket key still works
    assert inline_ticket.build("s\nAC-1: x", key="PROJ-42")["key"] == "PROJ-42"


def test_run_inline_queue_mode(tmp_path):
    env = {**os.environ, "AIQE_QUEUE_FILE": str(tmp_path / "queue.json")}
    r = subprocess.run([sys.executable, str(ROOT / "bin/qa.py"), "run-inline",
                        "Inline story\nAC-1: works", "--key", "ADHOC-T1",
                        "--repos", "orders-api", "--queue"],
                       capture_output=True, text=True, cwd=ROOT, env=env,
                       stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stdout + r.stderr
    assert "inline ticket: ADHOC-T1" in r.stdout and "queued" in r.stdout
    items = json.load(open(tmp_path / "queue.json"))
    assert items[0]["target"] == "ADHOC-T1" and items[0]["inline_file"]
    assert json.load(open(items[0]["inline_file"]))["summary"] == "Inline story"


# --- issue-type guidance (P0 item 3) ---------------------------------------------

def test_issue_type_guidance_prompts_exist():
    for name, marker in [("story", "Extend existing mapped tests"),
                         ("bug", "REGRESSION test"),
                         ("security", "NEGATIVE tests")]:
        p = ROOT / f"prompts/issue-types/{name}.md"
        assert p.exists() and marker in p.read_text(encoding="utf-8")
