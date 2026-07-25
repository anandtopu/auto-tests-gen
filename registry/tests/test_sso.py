"""Reverse-proxy SSO header auth on the dashboard (product-direction H1).

The contract under test, against a REAL server process:
  - AIQE_SSO_HEADER set + header present  -> authenticated, identity exposed
  - AIQE_SSO_HEADER set + header absent   -> 401 (FAILS CLOSED — a proxy
    misconfiguration must never silently expose the dashboard)
  - Bearer AIQE_UI_TOKEN still works with SSO on (API clients bypass the proxy)
  - AIQE_SSO_HEADER unset                 -> today's behavior, untouched
  - the SSO identity signs approvals that don't name an actor
"""
import json, os, pathlib, socket, subprocess, sys, time, urllib.request

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "engine/lib"))


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def server(tmp_path):
    """Start the real dashboard server; yields (base_url, env_updater, reviews_file)."""
    procs = []

    def start(**env_extra):
        port = _free_port()
        reviews = tmp_path / f"reviews-{port}.json"
        env = {**os.environ, "AIQE_UI_PORT": str(port), "AIQE_MOCK": "1",
               "AIQE_REVIEWS_FILE": str(reviews), **env_extra}
        env.pop("AIQE_UI_TOKEN", None)
        env.pop("AIQE_SSO_HEADER", None)
        env.update(env_extra)
        proc = subprocess.Popen([sys.executable, str(ROOT / "bin/dashboard_server.py")],
                                cwd=ROOT, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                                env=env)
        procs.append(proc)
        base = f"http://127.0.0.1:{port}"
        for _ in range(60):
            try:
                _req(base + "/api/version", headers={"X-Test-User": "probe"},
                     token=env_extra.get("AIQE_UI_TOKEN"))
                break
            except Exception:
                if proc.poll() is not None:
                    raise RuntimeError("server died on startup")
                time.sleep(0.25)
        return base, reviews

    yield start
    for p in procs:
        p.terminate()
        try:
            p.wait(timeout=10)
        except subprocess.TimeoutExpired:
            p.kill()


def _req(url, method="GET", headers=None, body=None, token=None):
    req = urllib.request.Request(url, method=method,
                                 data=json.dumps(body).encode() if body else None)
    for k, v in (headers or {}).items():
        req.add_header(k, v)
    if token:
        req.add_header("Authorization", f"Bearer {token}")
    if body:
        req.add_header("Content-Type", "application/json")
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def test_sso_header_authenticates_and_names_the_user(server):
    base, _ = server(AIQE_SSO_HEADER="X-Test-User")
    code, body = _req(base + "/api/version", headers={"X-Test-User": "ana@corp"})
    assert code == 200
    assert body["user"] == "ana@corp" and body["sso"] is True


def test_sso_fails_closed_without_the_header(server):
    """A misconfigured proxy must yield 401, never an open dashboard."""
    base, _ = server(AIQE_SSO_HEADER="X-Test-User")
    code, body = _req(base + "/api/version")
    assert code == 401
    assert "X-Test-User" in body.get("error", ""), \
        "the 401 must name the missing header so the misconfig is diagnosable"


def test_bearer_token_still_works_with_sso_on(server):
    base, _ = server(AIQE_SSO_HEADER="X-Test-User", AIQE_UI_TOKEN="tok123")
    code, body = _req(base + "/api/version", token="tok123")
    assert code == 200 and body["user"] == "token-client"


def test_without_sso_config_behavior_is_unchanged(server):
    base, _ = server()
    code, body = _req(base + "/api/version")
    assert code == 200 and body["sso"] is False and body["user"] == ""


def test_sso_identity_signs_review_marks(server):
    base, reviews = server(AIQE_SSO_HEADER="X-Test-User")
    code, _ = _req(base + "/api/review", method="POST",
                   headers={"X-Test-User": "lead@corp"},
                   body={"key": "SSO-1", "status": "approved"})
    assert code == 200
    data = json.loads(reviews.read_text(encoding="utf-8"))
    assert data["SSO-1"]["reviewer"] == "lead@corp", \
        "an approval without an explicit actor must be signed by the SSO user"


def test_explicit_by_still_beats_the_header(server):
    base, reviews = server(AIQE_SSO_HEADER="X-Test-User")
    code, _ = _req(base + "/api/review", method="POST",
                   headers={"X-Test-User": "lead@corp"},
                   body={"key": "SSO-2", "status": "in_review", "by": "delegate"})
    assert code == 200
    data = json.loads(reviews.read_text(encoding="utf-8"))
    assert data["SSO-2"]["reviewer"] == "delegate"


def test_deployment_doc_states_the_spoofing_boundary():
    """The header is trusted verbatim — enabling it on a directly-reachable server
    is the one way to get this wrong, and the doc must say so."""
    doc = (ROOT / "docs/deployment.md").read_text(encoding="utf-8")
    assert "AIQE_SSO_HEADER" in doc
    assert "spoofable" in doc and "Fails closed" in doc
