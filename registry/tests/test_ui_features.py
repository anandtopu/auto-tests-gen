"""UI feature set: curated durable guidance, plan-mode queue, PR coverage
report, OpenHands agent endpoint with description passthrough.

Server tests run against a REAL dashboard process (same pattern as
test_hooks_auth) so routing, auth defaults and content types are the ones a
browser would see.
"""
import json, os, pathlib, socket, subprocess, sys, time, urllib.error, urllib.request

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


def test_plan_mode_drains_to_a_draft_plan_end_to_end():
    """The UI 'Plan only' path: queue plan mode -> drain -> the real pipeline
    authors the plan and STOPS — plan state is draft, no gate ran."""

    def drain():
        return subprocess.run([sys.executable,
                               str(ROOT / "engine/lib/work_queue.py"), "run"],
                              cwd=ROOT, capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              stdin=subprocess.DEVNULL, timeout=600,
                              env={**os.environ, "AIQE_MOCK": "1"})

    drain()          # flush any items left pending by earlier suite tests
    item, _ = work_queue.add("plan", "PROJ-301", requested_by="e2e-test")
    r = drain()
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
                   "fetch-rel-known", "PR coverage report"):
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


def test_queue_endpoint_accepts_plan_mode(server):
    base = server()
    code, _, raw = _req(base + "/api/queue", "POST",
                        {"mode": "plan", "target": "ZZUI-2"})
    assert code == 200, raw
    item = json.loads(raw)["item"]
    assert item["mode"] == "plan"
    work_queue.remove(item["id"])
