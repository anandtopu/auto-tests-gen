"""Dashboard /hooks/* auth gating (Pass-6 fix F7), against a REAL server.

The OpenHands Agent Server sends no SSO header and no UI token, so /hooks/*
is gated on AIQE_HOOK_TOKEN (X-AIQE-Token or Bearer — the receiver contract)
instead of UI auth. The truth table under test:

  hook token set   + correct token          -> 200 (regardless of UI auth)
  hook token set   + wrong/missing token    -> 401
  no hook token    + UI auth configured     -> 401 (fails closed)
  no hook token    + no UI auth (dev)       -> 200 (open, like the receiver)

And /hooks must never satisfy UI auth by accident: a UI Bearer token is not a
hook credential.
"""
import json, os, pathlib, socket, subprocess, sys, time, urllib.error, urllib.request

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


@pytest.fixture
def server(tmp_path):
    procs = []

    def start(**env_extra):
        port = _free_port()
        env = {**os.environ, "AIQE_UI_PORT": str(port), "AIQE_MOCK": "1",
               "AIQE_OPENHANDS_STATE": str(tmp_path / f"oh-{port}.json")}
        env.pop("AIQE_UI_TOKEN", None)
        env.pop("AIQE_SSO_HEADER", None)
        env.pop("AIQE_HOOK_TOKEN", None)
        env.update(env_extra)
        proc = subprocess.Popen([sys.executable, str(ROOT / "bin/dashboard_server.py")],
                                cwd=ROOT, stdout=subprocess.DEVNULL,
                                stderr=subprocess.DEVNULL, stdin=subprocess.DEVNULL,
                                env=env)
        procs.append(proc)
        base = f"http://127.0.0.1:{port}"
        for _ in range(60):
            try:
                _post(base + "/hooks/openhands/events", [],
                      token=env_extra.get("AIQE_HOOK_TOKEN"))
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


def _post(url, body, token=None, header=None, bearer=None):
    req = urllib.request.Request(url, method="POST",
                                 data=json.dumps(body).encode())
    req.add_header("Content-Type", "application/json")
    if token:
        req.add_header("X-AIQE-Token", token)
    if bearer:
        req.add_header("Authorization", f"Bearer {bearer}")
    if header:
        req.add_header(*header)
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return r.status, json.loads(r.read().decode() or "{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read().decode() or "{}")


def test_hook_token_authenticates_even_with_ui_auth_on(server):
    base = server(AIQE_UI_TOKEN="ui-tok", AIQE_HOOK_TOKEN="hk")
    code, body = _post(base + "/hooks/openhands/events", [], token="hk")
    assert code == 200, body
    code, _ = _post(base + "/hooks/openhands/events", [], bearer="hk")
    assert code == 200, "Bearer is the form OpenHands' WebhookSpec can express"


def test_wrong_or_missing_hook_token_is_401(server):
    base = server(AIQE_HOOK_TOKEN="hk")
    assert _post(base + "/hooks/openhands/events", [], token="wrong")[0] == 401
    assert _post(base + "/hooks/openhands/events", [])[0] == 401


def test_ui_locked_without_hook_token_fails_closed(server):
    """UI auth on but no hook token: hooks must be CLOSED, not silently open."""
    base = server(AIQE_UI_TOKEN="ui-tok")
    code, body = _post(base + "/hooks/openhands/events", [])
    assert code == 401
    assert "AIQE_HOOK_TOKEN" in body.get("error", ""), \
        "the 401 must say how to configure hook auth"


def test_ui_token_is_not_a_hook_credential(server):
    base = server(AIQE_UI_TOKEN="ui-tok", AIQE_HOOK_TOKEN="hk")
    assert _post(base + "/hooks/openhands/events", [], bearer="ui-tok")[0] == 401


def test_dev_mode_stays_open_like_the_receiver(server):
    base = server()
    code, _ = _post(base + "/hooks/openhands/events", [])
    assert code == 200


def test_hooks_gate_never_leaks_into_api_routes(server):
    """A valid HOOK token must not authenticate /api/* when UI auth is on."""
    base = server(AIQE_UI_TOKEN="ui-tok", AIQE_HOOK_TOKEN="hk")
    code, _ = _post(base + "/api/review", {"key": "X-1", "status": "approved"},
                    token="hk", bearer="hk")
    assert code == 401, "hook credentials must never mutate dashboard state"
