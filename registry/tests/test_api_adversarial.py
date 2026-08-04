"""Adversarial UAT for the dashboard SERVER (review pass C, made permanent).

The server exposes every state-mutating operation in the platform: approve a
plan, queue a run, edit settings, launch an agent. These attacks target the
ways that authority leaks — auth that fails OPEN, an identifier that escapes
its directory, a body that is trusted because it happened to parse.

Runs its OWN server on a free port with its own state directories, so the real
estate and any server on :4999 are untouched.

THE HARNESS ASSERTS ITSELF FIRST. A server that never started refuses every
request by default, which would make every "must be refused" test pass for the
wrong reason — the trap the gate suite hit when a failed clone let its attacks
run against the scaffold. `live_server` fails loudly if the server is not
answering, and one test proves an authenticated request actually succeeds.

Auth is checked BEFORE routing in do_GET/do_POST, so the unauthenticated cases
below hold even for a path that does not exist; the authenticated cases assert
"not 200" / "below 500", which a 404 satisfies honestly.
"""
import json
import os
import pathlib
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
TOKEN = "adversarial-token-for-tests"


def _free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def _request(url, method="GET", body=None, headers=None, timeout=15):
    """(status, body_text). A refusal is a RESULT, not an exception.
    status 0 means the request never reached the server."""
    data = body.encode() if isinstance(body, str) else body
    req = urllib.request.Request(url, data=data, method=method,
                                 headers=headers or {})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")
    except (urllib.error.URLError, socket.timeout, OSError) as e:
        return 0, str(e)


@pytest.fixture(scope="module")
def live_server(tmp_path_factory):
    d = tmp_path_factory.mktemp("api-adv")
    (d / "runs").mkdir()
    port = _free_port()
    env = dict(os.environ,
               AIQE_MOCK="1", AIQE_UI_PORT=str(port), AIQE_UI_TOKEN=TOKEN,
               AIQE_PLAN_DIR=str(d / "plans"),
               AIQE_TESTPLAN_DIR=str(d / "testplans"),
               AIQE_REVIEWS_FILE=str(d / "runs/reviews.json"),
               AIQE_QUEUE_FILE=str(d / "runs/queue.json"),
               AIQE_OPENHANDS_DIR=str(d / "openhands"))
    env.pop("AIQE_SSO_HEADER", None)
    env.pop("AIQE_HOOK_TOKEN", None)          # hooks must stay CLOSED
    logfile = d / "server.log"
    log = open(logfile, "w", encoding="utf-8")
    proc = subprocess.Popen([sys.executable, str(ROOT / "bin/dashboard_server.py")],
                            cwd=ROOT, env=env, stdout=log,
                            stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    try:
        status = 0
        for _ in range(80):
            if proc.poll() is not None:
                break
            status, _ = _request(f"{base}/api/items",
                                 headers={"Authorization": f"Bearer {TOKEN}"},
                                 timeout=2)
            if status:
                break
            time.sleep(0.25)
        if not status:
            log.flush()
            pytest.fail("the dashboard server never came up — no attack ran:\n"
                        + logfile.read_text(encoding="utf-8",
                                            errors="replace")[:1500])
        yield base, d
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
        log.close()


def _auth():
    return {"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"}


# ---------------------------------------------------------- harness sanity
def test_an_authenticated_request_actually_succeeds(live_server):
    """Without this, every 'must be refused' assertion below could be passing
    because the server is dead rather than because it refused."""
    base, _ = live_server
    status, _ = _request(f"{base}/api/items", headers=_auth())
    assert status == 200


# ------------------------------------------------------- 1. auth fails closed
# Auth is checked BEFORE routing, so any path would answer 401 unauthenticated
# — which means a typo'd route passes this test while proving nothing. Every
# path below was verified to be a REAL mutating POST route, so a pass here says
# the actual operation is protected. (`/api/plans/approve` was in the first
# draft and does not exist; the plan lifecycle moves through `/api/plans/status`.)
@pytest.mark.parametrize("method,path,body", [
    ("GET", "/api/items", None),
    ("POST", "/api/queue", '{"target":"orders-api","pr":"201","mode":"pr"}'),
    ("POST", "/api/plans/status", '{"key":"PROJ-301","status":"approved"}'),
    ("POST", "/api/review", '{"key":"PROJ-301","status":"approved"}'),
    ("POST", "/api/settings", '{"ANTHROPIC_API_KEY":"attacker-supplied"}'),
    ("POST", "/api/repos/remove", '{"name":"e2e-api-tests-1"}'),
    ("POST", "/api/repos/scope", '{"name":"e2e-api-tests-1","scope":["x"]}'),
    ("POST", "/api/integrations/check", "{}"),
])
def test_unauthenticated_requests_are_refused(live_server, method, path, body):
    """Every mutating operation, not just the read path: an auth check that
    covers GET and forgets POST protects the least valuable surface."""
    base, _ = live_server
    status, _ = _request(base + path, method=method, body=body,
                         headers={"Content-Type": "application/json"})
    assert status == 401, f"{method} {path} answered {status} without a token"


@pytest.mark.parametrize("token", [
    "wrong", TOKEN + "x", TOKEN[:10], TOKEN.upper(), "", " " + TOKEN,
])
def test_near_miss_tokens_are_refused(live_server, token):
    """A prefix, a suffix, a case flip and a padded copy must all fail — the
    comparison must be exact, not a substring or a normalisation."""
    base, _ = live_server
    q = urllib.parse.urlencode({"token": token})
    status, _ = _request(f"{base}/api/items?{q}")
    assert status == 401
    status, _ = _request(f"{base}/api/items",
                         headers={"Authorization": f"Bearer {token}"})
    assert status == 401


def test_the_refusal_never_leaks_the_expected_token(live_server):
    base, _ = live_server
    status, text = _request(f"{base}/api/items")
    assert status == 401
    assert TOKEN not in text, "the 401 body echoed the expected token"


# ------------------------------------------------------------- 2. hook auth
@pytest.mark.parametrize("headers", [
    {"Content-Type": "application/json"},          # nothing
    {"Authorization": f"Bearer {TOKEN}"},          # a UI token
    {"X-AIQE-Token": "guessed"},                   # a wrong hook token
])
def test_hooks_stay_closed_without_their_own_token(live_server, headers):
    """/hooks/* is machine-to-machine and has its OWN contract. With UI auth
    configured and no AIQE_HOOK_TOKEN set it must fail CLOSED — and a UI token
    must not open it, or the two trust domains have quietly merged."""
    base, _ = live_server
    status, _ = _request(f"{base}/hooks/openhands/events", method="POST",
                         body="{}", headers=headers)
    assert status == 401


# -------------------------------------------------------- 3. path traversal
@pytest.mark.parametrize("key", [
    "../../etc/hosts", "..%2F..%2Fsecret", "PROJ-301/../../../x",
    "....//....//x", "/abs/path",
])
def test_traversal_keys_cannot_read_outside_their_directory(live_server, key):
    base, d = live_server
    q = urllib.parse.urlencode({"key": key})
    status, text = _request(f"{base}/api/plans/one?{q}", headers=_auth())
    if status == 200:
        payload = json.loads(text) if text.strip().startswith("{") else {}
        assert not payload.get("text"), f"traversal key served content: {key}"
    assert not list(d.parent.glob("hosts")), "a file escaped the state dir"


@pytest.mark.parametrize("name", ["../../evil", "..", "/etc", "a/b"])
def test_traversal_repo_names_are_refused(live_server, name):
    """Repo names reach the filesystem through curated guidance and notes."""
    base, _ = live_server
    status, _ = _request(f"{base}/api/repos/curated", method="POST",
                         body=json.dumps({"name": name, "filename": "AGENTS.md",
                                          "content": "x"}),
                         headers=_auth())
    assert status != 200, f"unknown/traversal repo name accepted: {name}"


# ------------------------------------------------------- 4. malformed input
@pytest.mark.parametrize("payload", [
    "not json at all", '{"target":', "[]", "null", "123", '""',
    '{"target":{"nested":true}}', '{"target":["a","b"]}', "{}",
])
def test_malformed_bodies_never_produce_a_server_error(live_server, payload):
    """A 4xx with a message is a correct answer. A 5xx means the body reached
    code that assumed its shape — and a partial write may already have landed."""
    base, d = live_server
    status, _ = _request(f"{base}/api/queue", method="POST", body=payload,
                         headers=_auth())
    assert status < 500, f"{payload!r} produced {status}"
    queue = d / "runs/queue.json"
    if queue.exists():
        json.loads(queue.read_text(encoding="utf-8"))     # never left corrupt


def test_a_large_body_is_handled_cleanly(live_server):
    """A big POST must be refused or rejected, not crash the server or hang."""
    base, _ = live_server
    payload = json.dumps({"target": "A" * 2_000_000})
    status, _ = _request(f"{base}/api/queue", method="POST", body=payload,
                         headers=_auth(), timeout=30)
    assert 0 < status < 500, f"a 2 MB body produced {status}"


# ----------------------------------------------------- 5. method confusion
def test_a_get_cannot_perform_a_mutating_action(live_server):
    """POST-shaped parameters on a GET must not queue work."""
    base, _ = live_server
    _, before = _request(f"{base}/api/queue", headers=_auth())
    _request(f"{base}/api/queue?target=orders-api&pr=201&mode=pr",
             headers=_auth())
    _, after = _request(f"{base}/api/queue", headers=_auth())
    assert before == after, "a GET mutated the work queue"


# ------------------------------------------------------------ 6. still alive
def test_the_server_survived_every_attack(live_server):
    """If any attack above killed the process, that IS the finding."""
    base, _ = live_server
    status, _ = _request(f"{base}/api/items", headers=_auth())
    assert status == 200, "the server did not survive the attacks above"

# ---- destructive request flags must not ride on Python truthiness -----------
def _flag():
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_ds_flag", ROOT / "bin/dashboard_server.py")
    # Importing the whole server module has side effects; read the function out
    # of the source instead and exec just it.
    src = (ROOT / "bin/dashboard_server.py").read_text(encoding="utf-8")
    start = src.index("def _json_flag(")
    end = src.index("def _classify_status(")
    ns = {}
    exec(src[start:end], ns)          # noqa: S102 - our own source, no input
    return ns["_json_flag"]


def test_a_string_false_never_triggers_a_destructive_flag():
    """`p.get("factory")` used Python truthiness, so the STRING "false" — or
    "no", or any spelling a caller might send MEANING the opposite — is truthy
    and would have emptied the repo registry and team notes.

    `dry` happens to fail safe under truthiness (any non-empty string means
    "preview"), which is exactly why the inconsistency survived: the harmless
    case looked like proof the pattern was fine.
    """
    f = _flag()
    # `unusable=True` on the falsey spellings, `unusable=False` on the truthy
    # ones: each spelling must be RECOGNIZED, not merely land on the right
    # answer because the safe default happened to agree. Asserting with the
    # default pointing the same way cannot tell those apart — a version that
    # recognized only "0" passed that test.
    for spelling in ("false", "False", "no", "off", "0", ""):
        assert f(spelling, unusable=True) is False, spelling
    for spelling in ("true", "TRUE", "yes", "on", "1"):
        assert f(spelling, unusable=False) is True, spelling


def test_real_booleans_and_absence_still_work():
    f = _flag()
    assert f(True, unusable=False) is True
    assert f(False, unusable=True) is False, "an explicit false must be honoured"
    assert f(None, unusable=True) is False, "absent means not asked for"
    assert f(1, unusable=False) is True and f(0, unusable=True) is False


def test_an_unreadable_value_resolves_per_flag():
    """The safe side differs by flag: do not destroy on a value we cannot read,
    but DO prefer the preview. Same rule as C13's env knobs — resolve toward
    the outcome you can recover from by running it again."""
    f = _flag()
    weird = {"nested": "object"}
    assert f(weird, unusable=False) is False, "destructive flag defaulted ON"
    assert f(weird, unusable=True) is True, "dry-run flag defaulted OFF"


def test_the_endpoint_wires_the_safe_side_to_each_flag():
    src = (ROOT / "bin/dashboard_server.py").read_text(encoding="utf-8")
    assert '_json_flag(p.get("factory"), unusable=False)' in src
    assert '_json_flag(p.get("force"), unusable=False)' in src
    assert '_json_flag(p.get("dry"), unusable=True)' in src
    assert 'if p.get("factory"):' not in src, "raw truthiness is back"


def test_run_progress_refuses_a_request_that_names_nothing(live_server):
    """GET /api/run-progress with neither key nor run must 400. Returning 200
    with whatever ran last in this checkout would answer a question the caller
    did not ask — and on a shared estate that is somebody else's run."""
    base, _ = live_server
    status, body = _request(f"{base}/api/run-progress", headers=_auth())
    assert status == 400, f"expected 400, got {status}: {body[:200]}"
    assert "required" in body


def test_run_progress_rejects_a_key_with_path_characters(live_server):
    base, _ = live_server
    for bad in ("../../etc/passwd", "a/b", "a b"):
        status, _ = _request(
            f"{base}/api/run-progress?key={urllib.parse.quote(bad)}", headers=_auth())
        assert status == 400, f"{bad!r} was accepted"
