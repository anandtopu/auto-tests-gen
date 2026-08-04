"""Regression tests for the external-integration validator
(engine/lib/integration_check.py). Checks must be read-only, never leak secrets,
and degrade to `skipped` rather than failing when a system isn't configured."""
import http.server
import os, pathlib, socket, subprocess, sys, threading

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
import integration_check as ic


class _TestHTTPServer(http.server.HTTPServer):
    """A throwaway server whose accept queue survives a loaded machine.

    `socketserver` defaults `request_queue_size` to 5. Under a full `make
    review` — parallel gates, a dashboard render, several suites — a client
    connect to one of these fixtures gets its SYN dropped and Windows reports
    "[WinError 10054] connection forcibly closed". That surfaced as a test
    failure in code the test was not exercising, three times now (2026-07-28,
    2026-07-30, and again here), each read as a transient.

    Same root cause as the dashboard server's own backlog fix. The readiness
    gate below solves a DIFFERENT race — the server not yet accepting — and
    cannot help once the queue is full.
    """
    request_queue_size = 128
    allow_reuse_address = True


@pytest.fixture(autouse=True)
def clean_env(monkeypatch):
    """No integration configured unless a test opts in."""
    for k in ("ANTHROPIC_API_KEY", "GITHUB_TOKEN", "BITBUCKET_TOKEN", "STASH_TOKEN",
              "STASH_URL", "STASH_PROJECT", "ATLASSIAN_MCP_TOKEN", "AIQE_SMOKE_TICKET",
              "CONFLUENCE_URL", "OPENHANDS_URL", "JENKINS_URL", "SLACK_WEBHOOK_URL",
              "SMTP_HOST", "SPLUNK_HEC_URL", "AIQE_SMOKE_REPO"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setattr(ic, "_load_env", lambda: None)   # ignore any real .env


# ------------------------------------------------------------------ shape

def test_nothing_configured_is_all_skipped_and_exit_zero():
    out = ic.run()
    assert out["summary"]["fail"] == 0
    assert out["summary"]["skipped"] == len(ic.CHECKS)
    assert {r["status"] for r in out["results"]} == {"skipped"}
    for r in out["results"]:
        assert r["name"] and r["detail"] and "id" in r


def test_every_check_is_registered_and_selectable():
    assert set(ic.CHECKS) >= {"llm", "scm", "jira", "confluence", "openhands",
                              "jenkins", "slack", "smtp", "splunk"}
    out = ic.run(["smtp"])
    assert len(out["results"]) == 1 and out["results"][0]["id"] == "smtp"
    # an unknown name must not silently return nothing
    assert len(ic.run(["nope"])["results"]) == len(ic.CHECKS)


def test_a_raising_check_is_reported_not_propagated(monkeypatch):
    monkeypatch.setitem(ic.CHECKS, "smtp", lambda: 1 / 0)
    out = ic.run(["smtp"])
    assert out["results"][0]["status"] == "fail"
    assert "check raised" in out["results"][0]["detail"]


def test_secrets_are_never_echoed(monkeypatch):
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "https://evil.example.com/T00/B00/sUpErSeCrEt")
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-TOPSECRET")
    blob = str(ic.run(["slack", "llm"]))
    assert "sUpErSeCrEt" not in blob and "TOPSECRET" not in blob


# ------------------------------------------------------------------ SMTP: real socket

def _smtp_stub():
    """A minimal SMTP listener: greet, answer EHLO, accept QUIT."""
    srv = socket.socket()
    srv.bind(("127.0.0.1", 0))
    srv.listen(1)

    def serve():
        try:
            conn, _ = srv.accept()
        except OSError:
            return
        conn.sendall(b"220 stub ESMTP\r\n")
        while True:
            try:
                data = conn.recv(1024)
            except OSError:
                break
            if not data:
                break
            up = data.upper()
            if up.startswith((b"EHLO", b"HELO")):
                conn.sendall(b"250-stub\r\n250 HELP\r\n")
            elif up.startswith(b"QUIT"):
                conn.sendall(b"221 Bye\r\n")
                break
            else:
                conn.sendall(b"250 OK\r\n")
        conn.close()

    threading.Thread(target=serve, daemon=True).start()
    return srv, srv.getsockname()[1]


def test_smtp_connects_without_sending_mail(monkeypatch):
    srv, port = _smtp_stub()
    try:
        monkeypatch.setenv("SMTP_HOST", "127.0.0.1")
        monkeypatch.setenv("SMTP_PORT", str(port))
        monkeypatch.setenv("SMTP_SECURITY", "none")
        r = ic.check_smtp()
        assert r["status"] == "ok", r
        assert "no mail sent" in r["detail"]
    finally:
        srv.close()


def test_smtp_failure_is_actionable(monkeypatch):
    monkeypatch.setenv("SMTP_HOST", "127.0.0.1")
    monkeypatch.setenv("SMTP_PORT", "9")          # discard port: nothing listening
    monkeypatch.setenv("SMTP_SECURITY", "none")
    monkeypatch.setattr(ic, "TIMEOUT", 2)
    r = ic.check_smtp()
    assert r["status"] == "fail"
    assert "cannot connect" in r["detail"] and r["hint"]


# ------------------------------------------------------------------ HTTP checks

class _Handler(http.server.BaseHTTPRequestHandler):
    code = 200

    def do_GET(self):
        self.send_response(self.code)
        self.end_headers()
        self.wfile.write(b"{}")

    def log_message(self, *a):
        pass


def _http_stub(code):
    h = type("H", (_Handler,), {"code": code})
    srv = _TestHTTPServer(("127.0.0.1", 0), h)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def test_http_check_reports_reachable(monkeypatch):
    srv, port = _http_stub(200)
    try:
        monkeypatch.setenv("OPENHANDS_URL", f"http://127.0.0.1:{port}")
        # Both must be explicit: the "-> make smoke-openhands" hint is only emitted
        # once a key is present, so relying on ambient .env made this pass or fail
        # depending on the machine. Mode too — a checkout set to `off` would skip.
        monkeypatch.setenv("OPENHANDS_API_KEY", "test-key")
        monkeypatch.setenv("AIQE_OPENHANDS", "auto")
        r = ic.check_openhands()
        assert r["status"] == "ok" and "reachable" in r["detail"]
        assert "smoke-openhands" in r["detail"]      # points at the deeper test
    finally:
        srv.shutdown()


def test_http_check_reachable_without_a_key_says_so(monkeypatch):
    """The other branch: reachable, but not yet able to start a conversation."""
    srv, port = _http_stub(200)
    try:
        monkeypatch.setenv("OPENHANDS_URL", f"http://127.0.0.1:{port}")
        monkeypatch.delenv("OPENHANDS_API_KEY", raising=False)
        monkeypatch.setenv("AIQE_OPENHANDS", "auto")
        monkeypatch.setattr(ic, "_load_env", lambda: None)   # ignore any real .env
        r = ic.check_openhands()
        assert r["status"] == "ok"
        assert "OPENHANDS_API_KEY not set" in r["detail"]
    finally:
        srv.shutdown()


def test_http_check_distinguishes_bad_credentials(monkeypatch):
    srv, port = _http_stub(401)
    try:
        monkeypatch.setenv("JENKINS_URL", f"http://127.0.0.1:{port}")
        r = ic.check_cicd()
        assert r["status"] == "fail"
        assert "credentials rejected" in r["detail"] and r["hint"]
    finally:
        srv.shutdown()


def test_http_check_reports_unreachable(monkeypatch):
    monkeypatch.setattr(ic, "TIMEOUT", 2)
    monkeypatch.setenv("CONFLUENCE_URL", "http://127.0.0.1:9")
    r = ic.check_confluence()
    assert r["status"] == "fail" and "cannot reach" in r["detail"]


# ------------------------------------------------------------------ policy

def test_slack_is_validated_without_posting(monkeypatch):
    monkeypatch.setenv("SLACK_WEBHOOK_URL", "http://not-a-slack-url/x")
    r = ic.check_slack()
    assert r["status"] == "fail" and "hooks.slack.com" in r["detail"]


def test_jira_token_without_url_fails_with_a_settings_hint(monkeypatch):
    """Token set but JIRA_URL missing is a misconfiguration, not 'not configured'."""
    monkeypatch.setenv("ATLASSIAN_MCP_TOKEN", "tok")
    monkeypatch.delenv("JIRA_URL", raising=False)
    r = ic.check_tracker()
    assert r["status"] == "fail" and "JIRA_URL" in r["hint"]


def test_jira_configured_without_smoke_ticket_reads_ok_unverified(monkeypatch):
    """Token + URL with no AIQE_SMOKE_TICKET: 'ok (configured/unverified)' — the
    old 'skipped' rendered as the misleading 'not configured' in the UI."""
    monkeypatch.setenv("ATLASSIAN_MCP_TOKEN", "tok")
    monkeypatch.setenv("JIRA_URL", "https://jira.example.com")
    monkeypatch.delenv("AIQE_SMOKE_TICKET", raising=False)
    r = ic.check_tracker()
    assert r["status"] == "ok" and "AIQE_SMOKE_TICKET" in r["hint"]
    assert "not verified" in r["detail"]


def test_checks_never_use_a_mutating_adapter_verb():
    """Guardrail: the validator must not comment, push, attach or send."""
    src = (ROOT / "engine/lib/integration_check.py").read_text(encoding="utf-8")
    body = src.split('"""', 2)[2]                  # skip the module docstring
    for verb in ('"comment"', '"set_status"', '"attach"', '"publish_doc"',
                 '"clone_rw"', '"open_pr"', "send_message", "sendmail"):
        assert verb not in body, f"validator must stay read-only: found {verb}"


def test_cli_runs_and_reports(tmp_path):
    r = subprocess.run([sys.executable, str(ROOT / "engine/lib/integration_check.py"),
                        "--json"], cwd=ROOT, capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, timeout=180)
    assert r.returncode == 0, r.stderr
    import json
    out = json.loads(r.stdout)
    assert "results" in out and "summary" in out and "mock_mode" in out


def test_a_failing_required_system_exits_non_zero(tmp_path):
    """`make check-integrations` is the pre-flight before switching to real
    mode, and the natural thing to put in a CI job. Its EXIT CODE is what a
    script reads.

    The zero cases were pinned on both sides — nothing configured
    (test_nothing_configured_is_all_skipped_and_exit_zero) and an optional
    system down (test_standalone.py::test_check_integrations_exits_zero_when_
    only_openhands_is_down). The non-zero case was not, so a regression to
    always-exit-0 would let a CI gate pass over a completely broken estate
    while the human-readable output still said [FAIL]. That is the C13 shape at
    the exit-code layer: unreachable reported as success to the only consumer
    that is a machine.

    Port 9 (discard) is closed on a normal host, matching the unreachable
    fixtures already used in this suite.
    """
    env = {**os.environ, "SPLUNK_HEC_URL": "http://127.0.0.1:9",
           "JENKINS_URL": "http://127.0.0.1:9"}
    env.pop("AIQE_SSO_HEADER", None)
    r = subprocess.run([sys.executable, str(ROOT / "engine/lib/integration_check.py")],
                       cwd=ROOT, capture_output=True, text=True, env=env,
                       stdin=subprocess.DEVNULL, timeout=180)
    assert r.returncode != 0, \
        "a configured, unreachable system exited 0 — a CI gate would pass:\n" + r.stdout[-600:]
    assert "[FAIL]" in r.stdout
    # Every failure must NAME its fix, not just the error. Asserted for BOTH
    # configured systems: an `or` here passed with one hint deleted.
    assert "check SPLUNK_HEC_URL" in r.stdout, "the Splunk failure names no fix"
    assert "check JENKINS_URL" in r.stdout, "the Jenkins failure names no fix"


def test_the_json_path_carries_the_same_exit_contract(tmp_path):
    """`--json` has its own `sys.exit`, and the source comment beside it states
    the contract: "a CI job consuming the JSON goes green on broken
    credentials". Nothing enforced it — mutating that line to `sys.exit(0)`
    left the whole suite green, because the text-path test above exercises a
    different branch. A machine-readable output is the one MOST likely to be
    wired into a gate.
    """
    import json as _json
    env = {**os.environ, "SPLUNK_HEC_URL": "http://127.0.0.1:9"}
    r = subprocess.run([sys.executable, str(ROOT / "engine/lib/integration_check.py"),
                        "--json"], cwd=ROOT, capture_output=True, text=True, env=env,
                       stdin=subprocess.DEVNULL, timeout=180)
    payload = _json.loads(r.stdout)
    assert payload["summary"]["fail"] >= 1, "the fixture did not produce a failure"
    assert r.returncode != 0, \
        "--json reported a failure in the body but exited 0 — a CI gate reading " \
        "the exit code passes over broken credentials"


def test_an_optional_system_down_does_not_change_the_exit_code(tmp_path):
    """The other half of the pair, asserted HERE too so the contract is legible
    in one place: optional degradation must never fail the pre-flight, or teams
    running without OpenHands cannot use this command at all."""
    env = {**os.environ, "OPENHANDS_URL": "http://127.0.0.1:9"}
    r = subprocess.run([sys.executable, str(ROOT / "engine/lib/integration_check.py")],
                       cwd=ROOT, capture_output=True, text=True, env=env,
                       stdin=subprocess.DEVNULL, timeout=180)
    assert r.returncode == 0, \
        "an optional system being down failed the pre-flight:\n" + r.stdout[-600:]
    assert "degraded" in r.stdout
