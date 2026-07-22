"""Regression tests for the external-integration validator
(engine/lib/integration_check.py). Checks must be read-only, never leak secrets,
and degrade to `skipped` rather than failing when a system isn't configured."""
import http.server
import pathlib, socket, subprocess, sys, threading

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))
import integration_check as ic


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
    srv = http.server.HTTPServer(("127.0.0.1", 0), h)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv, srv.server_address[1]


def test_http_check_reports_reachable(monkeypatch):
    srv, port = _http_stub(200)
    try:
        monkeypatch.setenv("OPENHANDS_URL", f"http://127.0.0.1:{port}")
        r = ic.check_openhands()
        assert r["status"] == "ok" and "reachable" in r["detail"]
        assert "smoke-openhands" in r["detail"]      # points at the deeper test
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


def test_jira_skips_when_no_ticket_to_read(monkeypatch):
    monkeypatch.setenv("ATLASSIAN_MCP_TOKEN", "tok")
    r = ic.check_tracker()
    assert r["status"] == "skipped" and "AIQE_SMOKE_TICKET" in r["hint"]


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
