"""OpenHands task skills + named agent presets.

Pins: every skill has valid frontmatter and carries the non-negotiables; the
path-skill generator never clobbers the hand-authored skills; agent presets build
messages that point at sanctioned entry points only; the launcher stays out of
the engine (standalone invariant) and works --dry with no network.
"""
import pathlib, re, subprocess, sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
import openhands_agents
import work_queue

SKILLS_DIR = ROOT / ".agents/skills"
GENERATED = {"e2e-api-conventions", "e2e-ui-conventions"}
TASK_SKILLS = {"ai-qe", "pr-review", "test-generation", "test-review",
               "test-coverage", "test-data-generation", "test-plan"}


def _frontmatter(p):
    text = p.read_text(encoding="utf-8")
    m = re.match(r"\A---\n(.*?)\n---\n", text, re.S)
    assert m, f"{p} has no YAML frontmatter"
    import yaml
    return yaml.safe_load(m.group(1)), text


def test_all_task_skills_exist_with_valid_frontmatter():
    found = {d.name for d in SKILLS_DIR.iterdir() if d.is_dir()}
    assert TASK_SKILLS <= found, f"missing skills: {TASK_SKILLS - found}"
    names = set()
    for d in sorted(SKILLS_DIR.iterdir()):
        fm, text = _frontmatter(d / "SKILL.md")
        assert fm.get("name") == d.name, f"{d.name}: frontmatter name mismatch"
        assert fm.get("description"), f"{d.name}: no description"
        assert len(text) > 400, f"{d.name}: skill body is empty-ish"
        assert fm["name"] not in names, f"duplicate skill name {fm['name']}"
        names.add(fm["name"])


def test_task_skills_carry_the_non_negotiables():
    """Every skill that can touch repos must restate the gate monopoly, and every
    skill that reads tickets/PRs must restate the data-not-instructions rule."""
    for name in ("pr-review", "test-generation", "test-data-generation", "test-plan"):
        _, text = _frontmatter(SKILLS_DIR / name / "SKILL.md")
        low = text.lower()
        assert "never" in low and ("push" in low or "gate" in low), name
        assert "data" in low and "instructions" in low, \
            f"{name}: missing the data-not-instructions framing"
    _, text = _frontmatter(SKILLS_DIR / "test-review" / "SKILL.md")
    assert "never marks `approved`" in text or "never approve" in text.lower(), \
        "test-review must forbid agent self-approval"


def test_generator_does_not_clobber_task_skills():
    r = subprocess.run([sys.executable, str(ROOT / "bin/gen_path_skills.py")],
                       cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                       stdin=subprocess.DEVNULL)
    assert r.returncode == 0, r.stderr
    for name in TASK_SKILLS:
        assert (SKILLS_DIR / name / "SKILL.md").exists(), \
            f"gen_path_skills.py deleted hand-authored skill {name}"


def test_agent_presets_target_sanctioned_entry_points():
    m = openhands_agents.build("test-generation", "orders-api", "201")
    assert "bash engine/pipeline.sh pr orders-api 201" in m
    m = openhands_agents.build("test-generation", "PROJ-301")
    assert "bash engine/pipeline.sh jira PROJ-301" in m
    m = openhands_agents.build("test-plan", "PROJ-301")
    assert "bash engine/pipeline.sh plan PROJ-301" in m
    m = openhands_agents.build("pr-review", "orders-api", "201")
    assert "pr-review" in m and "orders-api" in m and "201" in m
    m = openhands_agents.build("test-review", "PROJ-301")
    assert "never approve" in m.lower()


def test_every_preset_references_an_existing_skill():
    for name, a in openhands_agents.AGENTS.items():
        assert (SKILLS_DIR / a["skill"] / "SKILL.md").exists(), \
            f"agent {name} points at missing skill {a['skill']}"


def test_unknown_agent_and_missing_target_fail_cleanly():
    with pytest.raises(SystemExit):
        openhands_agents.build("no-such-agent", "x")
    with pytest.raises(SystemExit):
        openhands_agents.build("test-review")          # target required
    with pytest.raises(SystemExit):
        openhands_agents.build("test-generation")      # would build `pipeline.sh jira `
    with pytest.raises(SystemExit):
        openhands_agents.build("pr-review", "orders-api")   # PR number required


def test_qa_cli_dry_run_needs_no_network():
    r = subprocess.run([sys.executable, str(ROOT / "bin/qa.py"), "openhands-run",
                        "pr-review", "orders-api", "201", "--dry"],
                       cwd=ROOT, capture_output=True, text=True, encoding="utf-8",
                       stdin=subprocess.DEVNULL, timeout=60)
    assert r.returncode == 0, r.stderr
    assert "pr-review" in r.stdout and "orders-api" in r.stdout


def test_agents_module_keeps_the_standalone_invariant():
    """Message building lives in engine/lib but must never touch the client —
    test_standalone scans for the import; this pins the intent locally too."""
    src = (ROOT / "engine/lib/openhands_agents.py").read_text(encoding="utf-8")
    assert "openhands_" + "client" not in src
    assert "urllib" not in src and "requests" not in src


# ------------------------------- OpenHands interoperability (docs-driven)

def test_client_sends_both_documented_auth_schemes():
    """Cloud V1 authenticates with `Authorization: Bearer`; the self-hosted
    Agent Server uses `X-Session-API-Key` (docs.openhands.dev/sdk/arch/
    agent-server). Sending only one silently fails against the other kind of
    deployment — the same interoperability class as the conversations 405."""
    import openhands_client
    h = openhands_client._headers("k123")
    assert h["Authorization"] == "Bearer k123"
    assert h["X-Session-API-Key"] == "k123"
    assert openhands_client._headers("") == {
        "Content-Type": "application/json", "Accept": "application/json"}


def test_health_probes_the_documented_agent_server_paths():
    import openhands_client
    for path in ("/health", "/ready", "/server_info"):
        assert path in openhands_client._HEALTH_CANDIDATES


def test_bundled_skill_scripts_exist_and_are_referenced():
    """AgentSkills progressive disclosure: a skill may bundle scripts/ next to
    SKILL.md. Ours must exist, be shell-valid, and be referenced by the skill
    that ships them — an unreferenced script is dead weight the agent never runs."""
    import subprocess as sp
    for skill, script in (("pr-review", "gather-context.sh"),
                          ("test-coverage", "coverage-snapshot.sh")):
        p = SKILLS_DIR / skill / "scripts" / script
        assert p.exists(), f"{skill} is missing its bundled {script}"
        r = sp.run([str(work_queue.bash_exe()), "-n", str(p)],
                   capture_output=True, text=True, stdin=sp.DEVNULL)
        assert r.returncode == 0, f"{script} has a syntax error: {r.stderr}"
        body = (SKILLS_DIR / skill / "SKILL.md").read_text(encoding="utf-8")
        assert f"scripts/{script}" in body, \
            f"{skill}/SKILL.md must tell the agent to run its bundled script"


def test_bundled_scripts_are_read_only_by_construction():
    """A skill script the agent runs unattended must never write to a repo —
    the gate is the only writer (non-negotiable)."""
    for skill, script in (("pr-review", "gather-context.sh"),
                          ("test-coverage", "coverage-snapshot.sh")):
        body = (SKILLS_DIR / skill / "scripts" / script).read_text(encoding="utf-8")
        for forbidden in ("git commit", "git push", "qa.py map", "repos.py add",
                          "pipeline.sh", "rm -rf"):
            assert forbidden not in body, \
                f"{script} must stay read-only — found {forbidden!r}"


# ------------------------- Cloud start-task vs conversation id (user bug)

def _fake_cloud(handler_map):
    """A stand-in OpenHands Cloud that returns a START-TASK from the POST and only
    reveals the conversation id via the start-tasks endpoint — the real V1 shape."""
    import http.server, json as _json, threading

    class H(http.server.BaseHTTPRequestHandler):
        def log_message(self, *a): pass

        def _reply(self, obj, code=200):
            body = _json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def do_POST(self):
            self._reply(handler_map["post"])

        def do_GET(self):
            handler_map.setdefault("gets", []).append(self.path)
            self._reply(handler_map["get"])

    srv = http.server.HTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, f"http://127.0.0.1:{srv.server_port}"


def test_cloud_start_task_id_is_never_reported_as_the_conversation_id(monkeypatch):
    """Reported: 'Author via OpenHands' returned a conversation link that errored,
    while OpenHands had really created a conversation under a DIFFERENT id.

    Cause: the Cloud POST returns a start-task whose `id` is the TASK's. The old
    extraction took `id` as the conversation id, which also nulled start_task_id,
    so the resolver never ran and the URL was built from a task id.
    """
    import openhands_client as oc
    handlers = {"post": {"id": "task-AAA", "status": "pending"},
                "get": {"items": [{"id": "task-AAA",
                                   "app_conversation_id": "conv-REAL-123",
                                   "status": "running"}]}}
    srv, base = _fake_cloud(handlers)
    try:
        monkeypatch.setenv("OPENHANDS_URL", base)
        monkeypatch.setenv("OPENHANDS_API_KEY", "k")
        monkeypatch.setenv("OPENHANDS_CONVERSATIONS_PATH", "/api/v1/app-conversations")
        monkeypatch.setattr(oc, "POLL_INTERVAL_S", 0)
        r = oc.start("do the thing")
    finally:
        srv.shutdown()

    assert r["conversation_id"] == "conv-REAL-123", \
        "the resolver must return the REAL conversation id, not the start-task id"
    assert r["start_task_id"] == "task-AAA"
    assert "task-AAA" not in r["url"], "a URL must never be built from a task id"
    assert r["url"].endswith("conv-REAL-123")
    assert r["pending"] is False


def test_unresolved_cloud_start_reports_pending_with_no_fabricated_link(monkeypatch):
    """When the task has not produced a conversation yet, say so — do not invent an
    id or a link that will 404."""
    import openhands_client as oc
    handlers = {"post": {"id": "task-BBB", "status": "pending"},
                "get": {"items": [{"id": "task-BBB", "status": "pending"}]}}
    srv, base = _fake_cloud(handlers)
    try:
        monkeypatch.setenv("OPENHANDS_URL", base)
        monkeypatch.setenv("OPENHANDS_API_KEY", "k")
        monkeypatch.setenv("OPENHANDS_CONVERSATIONS_PATH", "/api/v1/app-conversations")
        monkeypatch.setattr(oc, "POLL_INTERVAL_S", 0)
        monkeypatch.setattr(oc, "POLL_ATTEMPTS", 2)
        r = oc.start("do the thing")
    finally:
        srv.shutdown()

    assert r["conversation_id"] == ""
    assert r["start_task_id"] == "task-BBB"
    assert r["url"] == "", "no link is better than a link that 404s"
    assert r["pending"] is True


def test_self_hosted_id_is_still_the_conversation_id(monkeypatch):
    """The self-hosted Agent Server returns the conversation directly — `id` there
    IS the conversation id, and the cloud fix must not regress it."""
    import openhands_client as oc
    handlers = {"post": {"id": "conv-selfhosted-1", "status": "running"}, "get": {}}
    srv, base = _fake_cloud(handlers)
    try:
        monkeypatch.setenv("OPENHANDS_URL", base)
        monkeypatch.setenv("OPENHANDS_API_KEY", "k")
        monkeypatch.setenv("OPENHANDS_CONVERSATIONS_PATH", "/api/conversations")
        r = oc.start("do the thing")
    finally:
        srv.shutdown()

    assert r["conversation_id"] == "conv-selfhosted-1"
    assert r["start_task_id"] is None and r["pending"] is False
    assert r["url"].endswith("conv-selfhosted-1")


def test_launch_toasts_never_claim_an_id_they_do_not_have():
    src = (ROOT / "bin/dashboard.py").read_text(encoding="utf-8")
    assert "function ohLaunchMsg(" in src
    assert "r.conversation_id || 'ok'" not in src, \
        "'ok' hid an unresolved launch behind a success message"
    # Both launch buttons call it (the third match is the definition itself).
    assert src.count("toast(ohLaunchMsg(r,") == 2, "both launch buttons must use it"
