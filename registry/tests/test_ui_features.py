"""UI feature set: curated durable guidance, plan-mode queue, PR coverage
report, OpenHands agent endpoint with description passthrough.

Server tests run against a REAL dashboard process (same pattern as
test_hooks_auth) so routing, auth defaults and content types are the ones a
browser would see.
"""
import json, os, pathlib, re, socket, subprocess, sys, time, urllib.error, urllib.request

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
import curated_guidance
import openhands_agents
import pr_comment
import repo_admin
import work_queue


# -------------------------------------------------------- curated guidance

def test_curated_save_get_delete_roundtrip():
    try:
        r = curated_guidance.save("e2e-api-tests-2", "AGENTS.md", "# curated\nrules\n")
        assert r["path"] == "knowledge/curated/e2e-api-tests-2/AGENTS.md"
        assert curated_guidance.get("e2e-api-tests-2")["AGENTS.md"].startswith("# curated")
        # empty content deletes
        r = curated_guidance.save("e2e-api-tests-2", "AGENTS.md", "  ")
        assert r["deleted"]
        assert "AGENTS.md" not in curated_guidance.get("e2e-api-tests-2")
    finally:
        curated_guidance.drop("e2e-api-tests-2")


def test_curated_rejects_unknown_repo_and_bad_filename():
    with pytest.raises(SystemExit):
        curated_guidance.save("no-such-repo", "AGENTS.md", "x")
    with pytest.raises(SystemExit):
        curated_guidance.save("orders-api", "evil.sh", "x")


def test_curated_outranks_generated_but_never_a_repo_owned_file(monkeypatch):
    # e2e-api-tests-2 ships no guidance of its own -> curated must win over
    # whatever the platform generated for it.
    try:
        curated_guidance.save("e2e-api-tests-2", "AGENTS.md", "# curated wins\n")
        rows = repo_admin.repo_local_files("e2e-api-tests-2")
        agents = next((r for r in rows
                       if pathlib.Path(r["path"]).name == "AGENTS.md"), None)
        assert agents and "curated" in agents["path"], \
            f"curated must be the effective AGENTS.md source: {agents}"
        # orders-api DOES ship its own CLAUDE.md (demo fixture stands in for the
        # repo-owned file only when no clone/cache exists; with a workspace clone
        # present the clone wins) — curating CLAUDE.md for a repo whose checkout
        # carries one must not shadow the checkout's copy.
        if (ROOT / "workspace/src/orders-api/CLAUDE.md").exists():
            curated_guidance.save("orders-api", "CLAUDE.md", "# should not win\n")
            rows = repo_admin.repo_local_files("orders-api")
            claude = next(r for r in rows
                          if pathlib.Path(r["path"]).name == "CLAUDE.md")
            assert "curated" not in claude["path"], \
                "a repo-owned file must always beat the curated copy"
    finally:
        curated_guidance.drop("e2e-api-tests-2")
        curated_guidance.drop("orders-api")


def test_factory_reset_drops_curated_but_plain_clear_keeps_it(tmp_path):
    import demo_data
    (tmp_path / "reports/runs").mkdir(parents=True)
    cur = tmp_path / "knowledge/curated/some-repo"
    cur.mkdir(parents=True)
    (cur / "AGENTS.md").write_text("# durable\n", encoding="utf-8")
    demo_data.clear(root=tmp_path)                       # plain clear
    assert (cur / "AGENTS.md").exists(), "plain clear must KEEP curated files"
    demo_data.clear(root=tmp_path, factory=True)
    assert not cur.exists(), "factory reset must remove curated guidance"


# ------------------------------------------------------------- queue: plan

def test_queue_accepts_plan_mode():
    item, fresh = work_queue.add("plan", "ZZUI-1", requested_by="test")
    try:
        assert fresh and item["mode"] == "plan" and item["target"] == "ZZUI-1"
    finally:
        work_queue.remove(item["id"])


def test_plan_mode_drains_to_a_draft_plan_end_to_end(tmp_path, monkeypatch):
    """The UI 'Plan only' path: queue plan mode -> drain -> the real pipeline
    authors the plan and STOPS — plan state is draft, no gate ran.

    The queue is ISOLATED to a tmp file (AIQE_QUEUE_FILE): draining the shared
    reports/runs/queue.json here would execute any real pending work in forced
    mock mode and mark it done behind the user's back."""
    qfile = tmp_path / "queue.json"
    monkeypatch.setattr(work_queue, "FILE", qfile)
    # force: this test's subject is queue DRAINING. Whether PROJ-301 is approved
    # depends on which tests ran before this one, and the approved-plan guard
    # (test_data_integrity) would otherwise make the enqueue order-dependent.
    item, _ = work_queue.add("plan", "PROJ-301", requested_by="e2e-test", force=True)
    r = subprocess.run([sys.executable,
                        str(ROOT / "engine/lib/work_queue.py"), "run"],
                       cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace",
                       stdin=subprocess.DEVNULL, timeout=600,
                       env={**os.environ, "AIQE_MOCK": "1",
                            "AIQE_QUEUE_FILE": str(qfile)})
    assert r.returncode == 0, r.stdout + r.stderr
    # Read the state FILE, not the plan_state module: an earlier suite test may
    # have imported plan_state under a tmp-path env and module caching would
    # leave the in-process view pointing at that test's store.
    state = json.loads((ROOT / "reports/plans/state.json")
                       .read_text(encoding="utf-8"))
    st = state.get("PROJ-301", {})
    assert st.get("status") == "draft", f"plan must stop at draft: {st}"
    assert "GATE_STATUS" not in r.stdout, "plan mode must never reach a gate"


def test_curated_content_reaches_the_estate_agents_md():
    """Curated guidance is a phase-context source: saving it and regenerating
    must land the content in the estate AGENTS.md every LLM phase reads."""
    marker = "zz-curated-e2e-marker-4711"
    try:
        curated_guidance.save("e2e-api-tests-2", "AGENTS.md", f"# {marker}\n")
        r = subprocess.run([sys.executable, str(ROOT / "bin/gen_agents_md.py")],
                           cwd=ROOT, capture_output=True, text=True,
                           encoding="utf-8", stdin=subprocess.DEVNULL, timeout=120)
        assert r.returncode == 0, r.stderr
        assert marker in (ROOT / "AGENTS.md").read_text(encoding="utf-8")
    finally:
        curated_guidance.drop("e2e-api-tests-2")
        subprocess.run([sys.executable, str(ROOT / "bin/gen_agents_md.py")],
                       cwd=ROOT, capture_output=True, stdin=subprocess.DEVNULL)


def test_queue_still_rejects_unknown_modes():
    with pytest.raises(SystemExit):
        work_queue.add("bogus", "X")


def test_prune_done_trims_history_but_never_pending_work(monkeypatch, tmp_path):
    """Data retention: done queue items accumulate forever without this; work
    (queued/failed/running) must never be touched."""
    qfile = tmp_path / "queue.json"
    monkeypatch.setattr(work_queue, "FILE", qfile)
    import json as _json, time as _t
    items = ([{"id": f"d{i}", "mode": "jira", "target": f"K-{i}", "pr": None,
               "release": "", "requested_by": "t", "status": "done",
               "ts": i, "finished": i} for i in range(10)] +
             [{"id": "q1", "mode": "jira", "target": "K-q", "pr": None,
               "release": "", "requested_by": "t", "status": "queued", "ts": 99},
              {"id": "f1", "mode": "jira", "target": "K-f", "pr": None,
               "release": "", "requested_by": "t", "status": "failed", "ts": 98}])
    qfile.write_text(_json.dumps(items), encoding="utf-8")
    r = work_queue.prune_done(keep=3)
    assert r == {"kept": 3, "removed": 7}
    left = _json.loads(qfile.read_text(encoding="utf-8"))
    statuses = [i["status"] for i in left]
    assert statuses.count("done") == 3
    assert "queued" in statuses and "failed" in statuses, \
        "pending work must survive history pruning"
    kept_done = sorted(i["id"] for i in left if i["status"] == "done")
    assert kept_done == ["d7", "d8", "d9"], "must keep the NEWEST done items"


# ------------------------------------------------------ pr coverage report

def _record(tests, gates, key="PR-x-9"):
    return {"run_id": "r1", "ts": 1, "trigger": {"type": "pr", "key": key},
            "cost_usd": 0.25,
            "phases": [
                {"name": "triage", "contract": {"impact": "create",
                                                "areas": ["orders discounts"]}},
                {"name": "generate", "contract": {"tests": tests,
                                                  "open_questions": []}},
                {"name": "validate", "contract": {"passed": 2, "failed": 0}},
                {"name": "critic", "contract": {"score": 0.9, "verdict": "accept"}}],
            "gates": gates}


def test_from_record_rebuilds_the_full_delta():
    md = pr_comment.from_record(_record(
        [{"file": "suites/a.spec.js", "action": "created"}],
        [{"test_repo": "e2e-api-tests-1", "status": "committed",
          "commit": "abc1234def"}]))
    assert "E2E coverage delta for PR-x-9" in md
    assert "1 created · 0 updated" in md
    assert "orders discounts" in md
    assert "✅ e2e-api-tests-1: committed `abc1234`" in md
    assert "critic (advisory): 0.9 accept" in md
    assert "run cost: $0.25" in md
    assert "test/PR-x-9-ai-qe" in md


def test_from_record_matches_the_live_composer(tmp_path):
    """build() (live, from out/ scratch) and from_record() (after the fact, from
    the run record) must produce the SAME report for the same run."""
    rec = _record([{"file": "suites/a.spec.js", "action": "created"}],
                  [{"test_repo": "e2e-api-tests-1", "status": "committed",
                    "commit": "abc1234def"}])
    out = tmp_path / "out"
    out.mkdir()
    for name in ("triage", "generate", "validate", "critic"):
        c = next(p["contract"] for p in rec["phases"] if p["name"] == name)
        (out / f"{name}.contract.json").write_text(json.dumps(c), encoding="utf-8")
    (out / "gate_results.tsv").write_text(
        "e2e-api-tests-1\tcommitted\t0\tabc1234def\n", encoding="utf-8")
    (out / "cost.tsv").write_text("generate\t0.250000\t1\t1\n", encoding="utf-8")
    live = pr_comment.build(tmp_path, "r1", "PR-x-9")
    replay = pr_comment.from_record(rec)
    assert live == replay, "the two composition paths must not drift"


def test_dashboard_page_carries_every_new_feature_surface():
    """The static page generator must keep rendering all the new UI surfaces
    (regression pin — a lost script block or section is invisible to pytest
    otherwise) and its JS must stay parseable."""
    r = subprocess.run([sys.executable, str(ROOT / "bin/dashboard.py")],
                       cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", stdin=subprocess.DEVNULL, timeout=300)
    assert r.returncode == 0, r.stderr
    html = (ROOT / "reports/dashboard.html").read_text(encoding="utf-8")
    for marker in ("Curated guidance file", "cur-save", "cur-export",
                   "Plan only", "plan-author-oh", "inl-plan-oh",
                   "fetch-rel-known", "PR coverage report",
                   "plan-spec", "plan-ambiguities",
                   # view persistence: mutation reloads (repo remove, settings
                   # save/clear) must return to the view they started from,
                   # not dump the user on Overview
                   "history.replaceState", "location.hash.replace"):
        assert marker in html, f"UI surface lost from the page: {marker}"
    import re as _re
    script = max(_re.findall(r"<script>(.*?)</script>", html, _re.S), key=len)
    js = ROOT / "out/_uifeat.js"
    js.write_text(script, encoding="utf-8")
    try:
        chk = subprocess.run(["node", "--check", str(js)], capture_output=True,
                             text=True, encoding="utf-8",
                             stdin=subprocess.DEVNULL, timeout=60)
        assert chk.returncode == 0, f"page JS broken: {chk.stderr}"
    finally:
        js.unlink(missing_ok=True)


def test_from_record_stays_silent_on_no_change_runs():
    assert pr_comment.from_record(_record(
        [], [{"test_repo": "e2e-api-tests-1", "status": "no_changes",
              "commit": ""}])) == ""


# -------------------------------------- openhands agent: description = DATA

def test_test_plan_message_frames_the_description_as_data():
    msg = openhands_agents.build("test-plan", "PROJ-9",
                                 description="Refunds return 500.\nAC-1: 400.")
    assert "pipeline.sh plan PROJ-9" in msg
    assert "TICKET DESCRIPTION (DATA)" in msg
    assert "never instructions" in msg
    assert "Refunds return 500." in msg


def test_description_is_ignored_for_review_agents():
    msg = openhands_agents.build("pr-review", "orders-api", "201",
                                 description="sneaky text")
    assert "sneaky text" not in msg


# ------------------------------------------ partial success on clone failure

def test_clone_failure_skips_the_repo_but_commits_the_rest():
    """Journey 3 trap: a mapped test repo whose clone fails (bad creds, renamed
    slug, no material yet) must not kill the work every OTHER repo gets. The
    failed repo enters the record as clone_failed and the run is flagged."""
    import repo_admin
    try:
        repo_admin.upsert_test("zz-nofetch", layer="api",
                               framework="playwright-api", scm="stash",
                               url="ZZ/zz-nofetch")
        repo_admin.set_scope("zz-nofetch", ["orders-api"])
        r = subprocess.run([work_queue.bash_exe(), "engine/pipeline.sh",
                            "pr", "orders-api", "201"],
                           cwd=ROOT, capture_output=True, text=True,
                           encoding="utf-8", errors="replace",
                           stdin=subprocess.DEVNULL, timeout=600,
                           env={**os.environ, "AIQE_MOCK": "1"})
        assert r.returncode == 0, r.stdout[-400:] + r.stderr[-400:]
        assert "GATE_STATUS=COMMITTED" in r.stdout, "good repos must still commit"
        assert "clone failed for test repo 'zz-nofetch'" in r.stdout
        # Select THIS run's record by id — "newest file" belongs to whichever
        # pipeline-running test finished last on the shared estate.
        import re as _re
        m = _re.search(r"AI-QE run (\S+) for", r.stdout)
        assert m, f"no run id in output: {r.stdout[-400:]}"
        rec_path = ROOT / f"reports/runs/{m.group(1)}.json"
        assert rec_path.exists(), f"no record for run {m.group(1)}"
        d = json.loads(rec_path.read_text(encoding="utf-8"))
        st = {g["test_repo"]: g["status"] for g in d["gates"]}
        assert st.get("zz-nofetch") == "clone_failed"
        assert "committed" in st.values()
        assert d["overall"] == "quarantined", \
            "a clone failure must flag the run for attention"
    finally:
        import repo_admin as ra
        try:
            ra.remove_test("zz-nofetch", force=True)
        except SystemExit:
            pass


# ------------------------------------------------- J6: ticket linking comment

def test_ticket_comment_links_plan_and_tests():
    import importlib, plan_state as ps
    importlib.reload(ps)                       # escape any tmp-path env cache
    text = ps.ticket_comment("PROJ-301")
    assert "AI-QE summary for PROJ-301" in text
    assert "testplans/PROJ-301.md" in text
    # tests + gates appear whenever a run exists for the key (demo estate does)
    if "E2E tests" in text:
        assert ".spec.js" in text and "Gate " in text and "Run record:" in text


def test_ticket_comment_posts_via_the_tracker_port():
    import importlib, plan_state as ps
    importlib.reload(ps)
    r = ps.post_ticket_comment("PROJ-301")     # mock tracker under AIQE_MOCK
    assert "PROJ-301" in r["result"]
    assert "AI-QE summary" in r["comment"]


# --------------------------------------------------------- server endpoints

def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def server():
    procs = []

    def start(**env_extra):
        port = _free_port()
        env = {**os.environ, "AIQE_UI_PORT": str(port), "AIQE_MOCK": "1"}
        for k in ("AIQE_UI_TOKEN", "AIQE_SSO_HEADER", "AIQE_OPENHANDS"):
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
                _req(base + "/api/version")
                break
            except (ConnectionError, urllib.error.URLError, OSError):
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


def _req(url, method="GET", body=None):
    req = urllib.request.Request(url, method=method,
                                 data=json.dumps(body).encode() if body else None)
    if body:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            return r.status, dict(r.headers), r.read()
    except urllib.error.HTTPError as e:
        return e.code, dict(e.headers), e.read()


def test_curated_endpoints_roundtrip_and_export(server):
    base = server()
    try:
        code, _, raw = _req(base + "/api/repos/curated", "POST",
                            {"repo": "e2e-api-tests-2", "file": "CLAUDE.md",
                             "content": "# via api\n"})
        assert code == 200, raw
        code, _, raw = _req(base + "/api/repos/curated?repo=e2e-api-tests-2")
        d = json.loads(raw)
        assert code == 200 and d["files"]["CLAUDE.md"] == "# via api\n"
        assert "effective" in d
        code, hdrs, raw = _req(
            base + "/api/repos/curated/export?repo=e2e-api-tests-2&file=CLAUDE.md")
        assert code == 200 and raw == b"# via api\n"
        assert "attachment" in hdrs.get("Content-Disposition", "")
        code, _, _raw = _req(base + "/api/repos/curated?repo=nope")
        assert code == 404
    finally:
        curated_guidance.drop("e2e-api-tests-2")
        # The save above regenerated AGENTS.md with the test content — restore
        # the estate file so committed state never carries "# via api".
        subprocess.run([sys.executable, str(ROOT / "bin/gen_agents_md.py")],
                       cwd=ROOT, capture_output=True, stdin=subprocess.DEVNULL)


def test_pr_coverage_endpoint(server):
    base = server()
    code, _, raw = _req(base + "/api/pr-coverage?key=NO-SUCH-KEY")
    assert code == 404
    # the demo estate always has a PR-orders-api-201 run on a verified checkout
    code, _, raw = _req(base + "/api/pr-coverage?key=PR-orders-api-201")
    if code == 200:                                     # tolerate a cleared estate
        d = json.loads(raw)
        assert "coverage delta" in d["markdown"] or "no tests" in d["markdown"]
        code, hdrs, _raw = _req(
            base + "/api/pr-coverage?key=PR-orders-api-201&download=1")
        assert code == 200 and "attachment" in hdrs.get("Content-Disposition", "")


def test_openhands_agent_endpoint_respects_the_off_posture(server):
    base = server(AIQE_OPENHANDS="off")
    code, _, raw = _req(base + "/api/openhands/agent", "POST",
                        {"agent": "test-plan", "target": "PROJ-1"})
    assert code == 409 and "disabled" in json.loads(raw)["error"]


def test_openhands_agent_endpoint_validates_before_any_network(server):
    base = server(AIQE_OPENHANDS="auto")
    code, _, raw = _req(base + "/api/openhands/agent", "POST",
                        {"agent": "no-such-agent", "target": "X"})
    assert code == 400 and "unknown agent" in json.loads(raw)["error"]


def test_demo_clear_endpoint_honors_dry_run(server):
    """A caller expressing dry-run intent must NEVER trigger a real clear —
    the endpoint used to silently ignore {"dry": true} and delete for real."""
    base = server()
    sentinel = ROOT / "reports/runs/zz-dry-sentinel.json"
    sentinel.write_text(json.dumps({"run_id": "zz-dry", "ts": 1,
                                    "trigger": {"type": "pr", "key": "ZZ-DRY"}}),
                        encoding="utf-8")
    try:
        code, _, raw = _req(base + "/api/demo/clear", "POST", {"dry": True})
        assert code == 200, raw
        d = json.loads(raw)
        assert d.get("removed", 0) > 0, "dry mode still PREVIEWS the targets"
        assert sentinel.exists(), \
            "dry:true deleted for real — the destructive-clear guard regressed"
    finally:
        sentinel.unlink(missing_ok=True)


def test_queue_endpoint_accepts_plan_mode(server):
    base = server()
    code, _, raw = _req(base + "/api/queue", "POST",
                        {"mode": "plan", "target": "ZZUI-2"})
    assert code == 200, raw
    item = json.loads(raw)["item"]
    assert item["mode"] == "plan"
    work_queue.remove(item["id"])


# ------------------------------- OpenHands conversation-endpoint negotiation

@pytest.fixture
def fake_openhands(monkeypatch):
    """A stand-in OpenHands whose per-path responses the test chooses."""
    import http.server, threading

    def start(responder):
        class H(http.server.BaseHTTPRequestHandler):
            def _respond(self):
                code, payload = responder(self.path)
                body = json.dumps(payload).encode() if payload is not None else b""
                self.send_response(code)
                if body:
                    self.send_header("Content-Type", "application/json")
                    self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if body:
                    self.wfile.write(body)

            def do_POST(self):
                n = int(self.headers.get("Content-Length", 0) or 0)
                self.rfile.read(n)
                self._respond()

            # GET too: Cloud V1 starting a conversation is a TWO-step exchange —
            # POST returns a start-task, and the conversation id only comes back from
            # GET .../start-tasks. A POST-only fake cannot model the real flow.
            def do_GET(self):
                self._respond()

            def log_message(self, *a):
                pass

        s = socket.socket(); s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]; s.close()
        srv = http.server.HTTPServer(("127.0.0.1", port), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        monkeypatch.setenv("OPENHANDS_URL", f"http://127.0.0.1:{port}")
        monkeypatch.setenv("OPENHANDS_API_KEY", "k")
        monkeypatch.delenv("OPENHANDS_CONVERSATIONS_PATH", raising=False)
        return srv

    yield start


def test_405_on_the_default_path_falls_back_to_the_other_shape(fake_openhands):
    """The reported bug: 'Author via OpenHands' surfaced a raw
    'HTTP 405: Method not allowed' — the deployment simply exposes the OTHER
    conversations endpoint. Negotiate instead of failing."""
    import importlib, openhands_client as oc
    importlib.reload(oc)
    monkeypatch_interval = getattr(oc, "POLL_INTERVAL_S", None)
    oc.POLL_INTERVAL_S = 0

    def responder(path):
        if path.startswith("/api/v1/app-conversations/start-tasks"):
            # Cloud only reveals the conversation id here — see start-task handling.
            return 200, {"items": [{"id": "task-42",
                                    "app_conversation_id": "conv-42",
                                    "status": "running"}]}
        if path == "/api/v1/app-conversations":
            return 200, {"id": "task-42", "status": "pending"}   # a START-TASK
        return 405, None

    srv = fake_openhands(responder)
    try:
        r = oc.start("hello", title="t")
        # Negotiation reached the other endpoint AND the id was resolved properly:
        # `id` from a Cloud POST is the task's, never the conversation's.
        assert r["conversation_id"] == "conv-42"
        assert r["start_task_id"] == "task-42"
        assert "task-42" not in r["url"]
    finally:
        srv.shutdown()
        if monkeypatch_interval is not None:
            oc.POLL_INTERVAL_S = monkeypatch_interval


def test_both_endpoints_failing_gives_an_actionable_message(fake_openhands):
    import importlib, openhands_client as oc
    importlib.reload(oc)
    srv = fake_openhands(lambda p: (405, None))
    try:
        with pytest.raises(RuntimeError) as e:
            oc.start("hello")
        assert "OPENHANDS_CONVERSATIONS_PATH" in str(e.value)
        assert "/api/conversations" in str(e.value)
    finally:
        srv.shutdown()


def test_an_explicit_path_is_respected_without_fallback(fake_openhands, monkeypatch):
    """A user who SET the path gets their path honored — no silent retries."""
    import importlib, openhands_client as oc
    importlib.reload(oc)
    srv = fake_openhands(lambda p: (405, None))
    monkeypatch.setenv("OPENHANDS_CONVERSATIONS_PATH", "/api/conversations")
    try:
        with pytest.raises(RuntimeError) as e:
            oc.start("hello")
        assert "HTTP 405" in str(e.value)
        assert "rejected both" not in str(e.value)
    finally:
        srv.shutdown()


# ---- journey review: the dashboard loaded data exactly once, and hid failures
def _ui():
    return (ROOT / "bin/dashboard.py").read_text(encoding="utf-8")


def test_entering_a_view_reloads_it():
    """Every loader used to fire once at page load and never again.

    Two failures came out of that. A loader that failed at load left its table
    permanently empty (its catch swallowed the error), and views whose entire
    purpose is "what is happening now" — the transaction log, alerts, the queue
    — silently showed a page-load snapshot. A stale activity log is worse than
    an empty one, because it looks current.
    """
    s = _ui()
    assert "function runViewLoaders(view)" in s
    assert "runViewLoaders(view);" in s, "go() must run the entering view's loaders"
    # The views whose content is time-sensitive must all be registered.
    for view in ("activity", "alerts", "queue", "plans", "trace", "cost", "specflow"):
        assert f"onEnter('{view}'" in s, f"{view} does not reload on entry"


def test_a_failed_loader_says_so_instead_of_rendering_an_empty_table():
    """An empty table is indistinguishable from "there is genuinely nothing
    here" — and on the activity and alert views those two readings lead to
    opposite actions."""
    s = _ui()
    assert "function loadFailed(" in s
    assert "display failure, not an empty result" in s
    # Every selector it is called with must name a table that actually exists,
    # or the message renders nowhere and we are back to a silent blank.
    import re
    sels = re.findall(r"loadFailed\('#([\w-]+) tbody'", s)
    assert sels, "no loader routes its failure to a table"
    for t in sels:
        assert f'table id="{t}"' in s, f"loadFailed targets #{t}, which does not exist"


def test_the_server_accepts_a_full_page_of_concurrent_loaders():
    """One page load fires ~10 requests at once. `socketserver` defaults the
    listen backlog to 5, so the rest overflowed the accept queue and Windows
    reset them — which surfaced as a permanently blank Activity view, not as an
    error anybody could read. Measured before the fix: 4 of 7 concurrent
    requests reset."""
    src = (ROOT / "bin/dashboard_server.py").read_text(encoding="utf-8")
    assert "class _Server(ThreadingHTTPServer)" in src
    m = re.search(r"request_queue_size\s*=\s*(\d+)", src)
    assert m and int(m.group(1)) >= 64, \
        "the backlog must comfortably exceed one page's worth of loaders"
    assert "_Server((host, port), Handler)" in src, "the subclass must be the one served"


def test_two_dashboards_cannot_quietly_share_a_port():
    """`HTTPServer` sets `allow_reuse_address = True`; the base `TCPServer` does
    not. On Linux that only shortens TIME_WAIT, but on Windows SO_REUSEADDR lets
    a second process bind an address already in LISTEN — so a second
    `make serve` appears to start and connections are split between the two,
    one of them running whatever code was on disk when it started.

    That failure reads as caching, not as two servers: this session lost time to
    it twice (a page served with an old column set, API routes 404ing that
    plainly existed). The UI_SCHEMA guard cannot catch it either, because the
    stale process answers the version probe too.
    """
    src = (ROOT / "bin/dashboard_server.py").read_text(encoding="utf-8")
    assert 'allow_reuse_address = sys.platform != "win32"' in src, \
        "the dashboard may bind a port another server already holds"
    # And the bind failure must be actionable rather than a raw WinError.
    assert "cannot bind" in src and "AIQE_UI_PORT" in src
