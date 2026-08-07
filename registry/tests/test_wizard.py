"""Guided run (wizard): the async status aggregation behind the two flows.

The wizard sequences EXISTING endpoints and polls one aggregated status, so the
contract under test is: does `wizard_status.build` report the real engine state
(queue / run records / plan state / review board) as steps a user can act on,
and does the endpoint validate its inputs?
"""
import json, os, pathlib, socket, subprocess, sys, time, urllib.error, urllib.request

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
import wizard_status


def _states(d):
    return {s["label"]: s["state"] for s in d["steps"]}


def test_unknown_key_is_all_pending_and_not_busy():
    d = wizard_status.build("ZZ-NOTHING-1", "pr")
    assert d["busy"] is False
    assert all(s["state"] == "pending" for s in d["steps"])
    assert d["tests"] == [] and d["run_id"] == ""


def test_pr_flow_reports_generation_gate_and_review(monkeypatch, tmp_path):
    """A committed PR run must light up tests + gate; review stays actionable."""
    runs = tmp_path / "reports/runs"
    runs.mkdir(parents=True)
    rec = {"run_id": "r1", "ts": 10, "trigger": {"type": "pr", "key": "PR-x-1"},
           "overall": "committed",
           "review": {"verdict": "needs_work", "findings": [{"finding": "gap"}],
                      "unresolved": [{"finding": "gap"}], "loops": 0,
                      "policy": "warn"},
           "phases": [{"name": "generate", "contract": {"tests": [
               {"file": "a.spec.js", "action": "created"},
               {"file": "b.spec.js", "action": "updated"}]}}],
           "gates": [{"test_repo": "e2e-api-tests-1", "status": "committed",
                      "commit": "abc1234"}]}
    (runs / "r1.json").write_text(json.dumps(rec), encoding="utf-8")
    (runs / "queue.json").write_text("[]", encoding="utf-8")
    (runs / "reviews.json").write_text(json.dumps(
        {"PR-x-1": {"status": "pending_review", "release": "2026.09"}}),
        encoding="utf-8")
    monkeypatch.setattr(wizard_status, "ROOT", tmp_path)
    import review_state, work_queue
    monkeypatch.setattr(review_state, "FILE", runs / "reviews.json")
    monkeypatch.setattr(work_queue, "FILE", runs / "queue.json")

    d = wizard_status.build("PR-x-1", "pr")
    st = _states(d)
    assert st["Generate E2E tests"] == "done"
    assert st["Agent review"] == "done"
    assert st["Quality gate"] == "done"
    assert st["Team review"] == "blocked", "pending review is the user's next action"
    labels = [s["label"] for s in d["steps"]]
    assert labels.index("Generate E2E tests") < labels.index("Agent review") \
        < labels.index("Quality gate")
    assert d["busy"] is False and d["release"] == "2026.09"
    assert {t["file"] for t in d["tests"]} == {"a.spec.js", "b.spec.js"}


def test_a_queued_run_marks_the_flow_busy(monkeypatch, tmp_path):
    """`busy` is what stops the page polling forever — it must be true only
    while real work is in flight."""
    runs = tmp_path / "reports/runs"
    runs.mkdir(parents=True)
    (runs / "queue.json").write_text(json.dumps(
        [{"id": "q1", "mode": "pr", "target": "x", "pr": "1",
          "status": "running", "ts": 1}]), encoding="utf-8")
    (runs / "reviews.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(wizard_status, "ROOT", tmp_path)
    import review_state, work_queue
    monkeypatch.setattr(review_state, "FILE", runs / "reviews.json")
    monkeypatch.setattr(work_queue, "FILE", runs / "queue.json")

    d = wizard_status.build("PR-x-1", "pr")
    assert d["busy"] is True
    assert _states(d)["Generate E2E tests"] == "running"


def test_a_quarantined_gate_shows_as_failed(monkeypatch, tmp_path):
    runs = tmp_path / "reports/runs"
    runs.mkdir(parents=True)
    (runs / "r1.json").write_text(json.dumps(
        {"run_id": "r1", "ts": 1, "trigger": {"type": "pr", "key": "PR-x-2"},
         "overall": "quarantined", "phases": [],
         "gates": [{"test_repo": "t1", "status": "quarantined", "commit": None}]}),
        encoding="utf-8")
    (runs / "queue.json").write_text("[]", encoding="utf-8")
    (runs / "reviews.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(wizard_status, "ROOT", tmp_path)
    import review_state, work_queue
    monkeypatch.setattr(review_state, "FILE", runs / "reviews.json")
    monkeypatch.setattr(work_queue, "FILE", runs / "queue.json")
    assert _states(wizard_status.build("PR-x-2", "pr"))["Quality gate"] == "failed"


def test_malformed_run_record_never_breaks_the_poll(monkeypatch, tmp_path):
    """Records are written non-atomically; a poll racing a live run must skip
    the half-written file, not 500 the wizard."""
    runs = tmp_path / "reports/runs"
    runs.mkdir(parents=True)
    (runs / "bad.json").write_text("{not json", encoding="utf-8")
    (runs / "queue.json").write_text("[]", encoding="utf-8")
    (runs / "reviews.json").write_text("{}", encoding="utf-8")
    monkeypatch.setattr(wizard_status, "ROOT", tmp_path)
    import review_state, work_queue
    monkeypatch.setattr(review_state, "FILE", runs / "reviews.json")
    monkeypatch.setattr(work_queue, "FILE", runs / "queue.json")
    d = wizard_status.build("PR-x-3", "pr")          # must not raise
    assert d["busy"] is False


def test_jira_flow_gates_on_human_approval():
    """The plan-first invariant surfaced as a step: a draft plan must render as
    BLOCKED (a human must act), never as done."""
    import plan_state
    live = plan_state.get("PROJ-301")
    d = wizard_status.build("PROJ-301", "jira")
    labels = [s["label"] for s in d["steps"]]
    assert labels[0] == "Author the test plan"
    approval = next(s for s in d["steps"] if s["label"] == "Human approval")
    if live.get("status") == "approved":
        assert approval["state"] == "done"
    elif live.get("status") in ("draft", "changes_requested"):
        assert approval["state"] == "blocked"
    assert any("Link" in s["label"] or "Linked" in s["label"] for s in d["steps"]), \
        "the JIRA flow must end at the ticket-linking step"


def test_reauthored_plan_hides_stale_generation_summary(monkeypatch, tmp_path):
    """Re-authoring invalidates the old generated run as one atomic UI fact.

    The ladder already hid the old tests and gate when ``generated_run`` was
    cleared, but it still surfaced the old run id, overall result, and agent
    review.  That made a new draft say ``Last run: committed`` even though the
    approval gate correctly blocked generation.
    """
    runs = tmp_path / "reports/runs"
    plans = tmp_path / "reports/plans"
    runs.mkdir(parents=True)
    plans.mkdir(parents=True)
    old = {
        "run_id": "old-generated-run", "ts": 10,
        "trigger": {"type": "jira", "key": "PROJ-301"},
        "overall": "committed",
        "review": {"verdict": "pass", "findings": [], "unresolved": [],
                   "policy": "warn"},
        "phases": [{"name": "generate", "contract": {"tests": [
            {"file": "old.spec.js", "action": "created"}]}}],
        "gates": [{"test_repo": "e2e-api-tests-1", "status": "committed"}],
    }
    (runs / "old.json").write_text(json.dumps(old), encoding="utf-8")
    (runs / "queue.json").write_text("[]", encoding="utf-8")
    (runs / "reviews.json").write_text("{}", encoding="utf-8")
    (plans / "state.json").write_text(json.dumps({
        "PROJ-301": {"status": "draft", "generated_run": None}
    }), encoding="utf-8")

    monkeypatch.setattr(wizard_status, "ROOT", tmp_path)
    import plan_state
    import review_state
    import work_queue
    monkeypatch.setattr(plan_state, "FILE", plans / "state.json")
    monkeypatch.setattr(review_state, "FILE", runs / "reviews.json")
    monkeypatch.setattr(work_queue, "FILE", runs / "queue.json")

    d = wizard_status.build("PROJ-301", "jira")
    assert d["run_id"] == "" and d["overall"] == ""
    assert d["tests"] == []
    states = _states(d)
    assert states["Generate E2E tests"] == "pending"
    assert states["Agent review"] == "pending"
    assert states["Quality gate"] == "pending"


# ------------------------------------------------------------- endpoint + UI

def _free_port():
    s = socket.socket(); s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]; s.close()
    return port


@pytest.fixture
def server():
    procs = []

    def start(**env_extra):
        port = _free_port()
        env = {**os.environ, "AIQE_UI_PORT": str(port), "AIQE_MOCK": "1"}
        for k in ("AIQE_UI_TOKEN", "AIQE_SSO_HEADER"):
            env.pop(k, None)
        env.update(env_extra)
        proc = subprocess.Popen([sys.executable, str(ROOT / "bin/dashboard_server.py")],
                                cwd=ROOT, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                                env=env)
        procs.append(proc)
        base = f"http://127.0.0.1:{port}"
        for _ in range(60):
            try:
                urllib.request.urlopen(base + "/api/version", timeout=5)
                break
            except Exception:
                if proc.poll() is not None:
                    raise RuntimeError("server died on startup")
                time.sleep(0.25)
        return base

    yield start
    for p in procs:
        p.terminate()
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            p.kill()


def _get(url):
    try:
        with urllib.request.urlopen(url, timeout=20) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def test_status_endpoint_serves_and_validates(server):
    base = server()
    code, body = _get(base + "/api/wizard/status?key=PROJ-301&mode=jira")
    assert code == 200 and "steps" in body and body["mode"] == "jira"
    code, _ = _get(base + "/api/wizard/status?key=X&mode=bogus")
    assert code == 400, "an unknown mode must not fall through to a default flow"
    code, _ = _get(base + "/api/wizard/status?key=../etc&mode=pr")
    assert code == 400, "keys are charset-validated"


def test_wizard_view_is_present_in_the_page():
    r = subprocess.run([sys.executable, str(ROOT / "bin/dashboard.py")],
                       cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", stdin=subprocess.DEVNULL, timeout=300)
    assert r.returncode == 0, r.stderr
    html = (ROOT / "reports/dashboard.html").read_text(encoding="utf-8")
    for marker in ('data-view="wizard"', "Guided run", "wz-start-pr",
                   "wz-start-plan", "wz-approve", "wz-generate", "wz-link",
                   "/api/wizard/status"):
        assert marker in html, f"wizard surface missing: {marker}"
    # the wizard must reuse existing endpoints, never invent new mutations
    for endpoint in ("/api/queue", "/api/plans/status", "/api/plans/generate",
                     "/api/plans/comment"):
        assert endpoint in html, f"wizard should drive {endpoint}"


def test_changing_a_wizard_target_clears_the_previous_result():
    """A result belongs to one target and must disappear when that target changes.

    Without this, a rejected second submission leaves the first PR's successful
    ladder and generated files beside the second PR's inputs, making the old
    evidence look like the new target's outcome.
    """
    r = subprocess.run([sys.executable, str(ROOT / "bin/dashboard.py")],
                       cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", stdin=subprocess.DEVNULL, timeout=300)
    assert r.returncode == 0, r.stderr
    html = (ROOT / "reports/dashboard.html").read_text(encoding="utf-8")
    assert "function wzResetTarget()" in html
    for field in ("wz-repo", "wz-pr", "wz-pr-ticket", "wz-key"):
        assert f"$('#{field}').addEventListener('input', wzResetTarget)" in html, \
            f"editing {field} can leave another target's result visible"
    assert "$('#wz-steps').innerHTML = ''" in html
    assert "$('#wz-result').innerHTML = ''" in html
    assert "wzRevision += 1" in html
    assert "revision !== wzRevision" in html, \
        "an in-flight poll can repaint the previous target after reset"
    assert "function wzSetPrSubmitting(disabled)" in html
    assert html.count("finally { wzSetPrSubmitting(false); }") == 2, \
        "both direct and plan-first PR intake must release their target lock"
