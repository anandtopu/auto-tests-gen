#!/usr/bin/env python3
"""TaskEvent receiver — the normalized webhook endpoint (architecture §5.10 rule 3).

Jira Automation rules, Bitbucket/Stash webhooks, and OpenHands conversation
starters all POST the same TaskEvent shape (triggers/task-event-schema.json):

  POST /hooks/taskevent   {"mode":"pr","repo":"orders-api","pr":201,"key":"PROJ-301","updated":"<sha>"}
                          {"mode":"jira","key":"PROJ-301","updated":"2026-07-21T10:00:00Z"}

Behavior: validate -> dedupe on sha256(mode|repo|pr|key-slot|updated|workflow_version),
where the PR key-slot stays empty for pre-A1.7 replay compatibility -> enqueue
into the work queue (NFR-6: webhook redeliveries are no-ops). With
AIQE_HOOK_AUTORUN=1 a queue drain is started after each accepted event.

It also ingests the OpenHands Agent Server event stream, which gives live
visibility into agent-driven runs instead of waiting for the pipeline's own run
record. Point WebhookSpec.base_url at <receiver>/hooks/openhands — OpenHands
appends the two paths itself:

  POST /hooks/openhands/events         buffered batches of agent events
  POST /hooks/openhands/conversations  conversation lifecycle records

Those are recorded (bounded, defensively) by engine/lib/openhands_events.py; they
never enqueue work, so a chatty agent cannot start pipeline runs.

Auth: set AIQE_HOOK_TOKEN. Senders may present it as X-AIQE-Token or as
Authorization: Bearer <token> — OpenHands sends whatever headers you configure in
WebhookSpec, and only the latter is expressible there.
Start: make hook-server   (default 127.0.0.1:4998, AIQE_HOOK_PORT to change)
"""
import hashlib
import json
import os
import pathlib
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine/lib"))
import fs_lock  # noqa: E402
import http_body  # noqa: E402
import openhands_events  # noqa: E402
import placeholder_secrets  # noqa: E402
import work_queue  # noqa: E402

TOKEN = os.environ.get("AIQE_HOOK_TOKEN", "")
AUTORUN = os.environ.get("AIQE_HOOK_AUTORUN", "0") == "1"
SEEN_FILE = pathlib.Path(os.environ.get("AIQE_HOOKS_SEEN",
                                        ROOT / "reports/runs/hooks-seen.json"))
SEEN_MAX = 500
drain_lock = threading.Lock()


def validate(ev):
    if not isinstance(ev, dict):
        return "TaskEvent body must be a JSON object"
    if "repo" in ev and not isinstance(ev["repo"], str):
        return "repo must be a string"
    if "pr" in ev and (isinstance(ev["pr"], bool)
                       or not isinstance(ev["pr"], (str, int))):
        return "pr must be a string or integer"
    if "key" in ev and not isinstance(ev["key"], str):
        return "key must be a string"
    for field in ("updated", "workflow_version"):
        if field in ev and not isinstance(ev[field], str):
            return f"{field} must be a string"
    mode = ev.get("mode")
    if mode == "pr":
        if not ev.get("repo", "").strip():
            return "pr mode requires repo as a non-empty string"
        pr = ev.get("pr")
        if pr is None or not str(pr).strip():
            return "pr mode requires repo and pr"
    elif mode == "jira":
        if not ev.get("key", "").strip():
            return "jira mode requires key"
    else:
        return "mode must be pr|jira"
    return None


def idempotency_key(ev):
    """Stable replay identity; optional PR ticket linkage is deliberately excluded.

    Before A1.7 every PR event occupied the key slot with an empty string. Keep
    that exact byte sequence so adding an explicit ticket does not change the
    identity of the SCM event. JIRA events still include their required key.
    """
    key_slot = "" if ev.get("mode") == "pr" else ev.get("key", "")
    parts = [ev.get("mode", ""), ev.get("repo", ""), str(ev.get("pr", "")),
             key_slot, ev.get("updated", ""), ev.get("workflow_version", "1")]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def _load_seen():
    # Guarded read: a torn write here silently emptied the dedupe window, so the
    # sender's retries were re-enqueued as fresh work (duplicate runs).
    return fs_lock.read_json_guarded(SEEN_FILE, [])


def already_seen(digest):
    with fs_lock.lock(SEEN_FILE):
        return digest in _load_seen()


def record_seen(digest):
    """Record only AFTER a successful enqueue — a delivery that failed to queue
    must stay unseen so the sender's retry is processed, not dropped as a dupe.
    Keeps a bounded window of recent digests."""
    with fs_lock.lock(SEEN_FILE):
        seen = _load_seen()
        if digest not in seen:
            seen = (seen + [digest])[-SEEN_MAX:]
            fs_lock.write_json_atomic(SEEN_FILE, seen, indent=None)


def handle_event(ev):
    """Pure handler: returns (http_code, response_dict). Unit-testable."""
    err = validate(ev)
    if err:
        return 400, {"error": err}
    digest = idempotency_key(ev)
    if already_seen(digest):
        return 200, {"accepted": False, "reason": "duplicate delivery (idempotent no-op)",
                     "idempotency_key": digest[:16]}
    try:
        if ev["mode"] == "pr":
            item, fresh = work_queue.add("pr", ev["repo"], str(ev["pr"]),
                                         requested_by="taskevent",
                                         ticket=ev.get("key") or None)
        else:
            item, fresh = work_queue.add("jira", ev["key"], requested_by="taskevent")
    except SystemExit as e:      # intake validation (unregistered repo / bad key)
        return 400, {"error": str(e)}
    if not fresh:
        record_seen(digest)
        return 200, {"accepted": False, "queued": False,
                     "reason": "matching work is already queued or running",
                     "item_id": item["id"],
                     "idempotency_key": digest[:16]}
    record_seen(digest)                     # durable enqueue first, then dedupe mark
    return 200, {"accepted": True, "queued": fresh, "item_id": item["id"],
                 "idempotency_key": digest[:16]}


def start_drain():
    if drain_lock.locked():
        return
    def drain():
        with drain_lock:
            subprocess.run([sys.executable, str(ROOT / "engine/lib/work_queue.py"),
                            "run"], cwd=ROOT, stdin=subprocess.DEVNULL)
    threading.Thread(target=drain, daemon=True).start()


class Handler(BaseHTTPRequestHandler):
    # Without this the socket has NO read deadline, and a client that declares a
    # large Content-Length then sends nothing holds a worker thread forever.
    # Measured on this endpoint: no response, connection kept open. A handful of
    # those stop the trigger ingress accepting PR and JIRA events at all.
    timeout = 30

    def log_message(self, fmt, *a):
        pass

    def _send(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        """Health/status only — this service takes work via POST. Unauthenticated by
        design so container probes need no token; it exposes no state or secrets
        (only whether auth is on), and never accepts work."""
        if self.path.split("?")[0] in ("/", "/healthz"):
            return self._send(200, {"service": "ai-qe-taskevent-receiver",
                                    "status": "ok",
                                    "endpoints": ["POST /hooks/taskevent",
                                                  "POST /hooks/openhands/events",
                                                  "POST /hooks/openhands/conversations"],
                                    "auth": bool(TOKEN),
                                    "autorun": AUTORUN})
        self._send(404, {"error": "GET / or /healthz; work is submitted via "
                                  "POST /hooks/taskevent"})

    def _authed(self):
        """X-AIQE-Token, or Authorization: Bearer — OpenHands' WebhookSpec can only
        send arbitrary headers, so Bearer is the form it can express."""
        if not TOKEN:
            return True
        if self.headers.get("X-AIQE-Token", "") == TOKEN:
            return True
        return self.headers.get("Authorization", "") == f"Bearer {TOKEN}"

    def do_POST(self):
        if not self._authed():
            return self._send(401, {"error": "missing or wrong credentials: send "
                                             "X-AIQE-Token or Authorization: Bearer"})
        path = self.path.split("?")[0].rstrip("/")
        # Route FIRST, then read with that route's limit: the 5 MB results cap
        # used to be checked after the whole body was already in memory, which
        # is not a cap. Everything else is a small JSON envelope.
        limit = (5 * 1024 * 1024 if path == "/hooks/ci/results"
                 else http_body.DEFAULT_MAX)
        raw, err = http_body.read_body(self, limit)
        if err:
            return self._send(*err)
        if raw is None:
            return                      # peer gone; nobody left to answer

        # --- CI results ingest (roadmap 1.1) --------------------------------------
        # Raw JUnit XML (or Jenkins JSON), NOT JSON-wrapped — so a CI job can post
        # `curl --data-binary @results.xml` with no shaping step. Routed BEFORE the
        # JSON parse below, which would otherwise 400 the XML at the front door.
        # Feeds catalog/health.json, which the scorecard's "Test health", the
        # critic's context and flake detection all read.
        if path == "/hooks/ci/results":
            if not raw.strip():
                return self._send(400, {"error": "empty body — post JUnit XML or "
                                                 "Jenkins JSON as the request body"})
            import tempfile
            import test_health
            try:
                suffix = ".json" if raw.lstrip()[:1] in (b"{", b"[") else ".xml"
                with tempfile.NamedTemporaryFile(mode="wb", suffix=suffix,
                                                 delete=False) as tf:
                    tf.write(raw)
                    tmp_path = tf.name
                try:
                    matched, unmatched = test_health.ingest(tmp_path)
                finally:
                    os.unlink(tmp_path)
            except Exception as e:                              # noqa: BLE001
                return self._send(400, {"error": f"could not parse results: "
                                                 f"{str(e)[:200]}"})
            # matched/unmatched in the response so the CI job's own log shows
            # whether catalog mapping worked — a silent 200 hides mapping rot.
            return self._send(200, {"ok": True, "matched": matched,
                                    "unmatched": unmatched})

        try:
            body = json.loads(raw or b"{}")
        except json.JSONDecodeError as e:
            return self._send(400, {"error": f"invalid JSON: {e}"})

        # --- OpenHands Agent Server event stream (observability only) -------------
        # These never enqueue work: a chatty agent must not be able to start runs.
        # Errors are swallowed into a 200 on purpose — a failing webhook endpoint
        # just makes OpenHands retry the same batch forever.
        if path in ("/hooks/openhands/events", "/hooks/openhands/conversations"):
            try:
                r = (openhands_events.record_events(body)
                     if path.endswith("/events")
                     else openhands_events.record_conversation(body))
                return self._send(200, {"ok": True, **r})
            except Exception as e:                                  # noqa: BLE001
                return self._send(200, {"ok": False,
                                        "error": f"not recorded: {str(e)[:120]}"})

        if path != "/hooks/taskevent":
            return self._send(404, {"error": "POST /hooks/taskevent, "
                                             "/hooks/openhands/events or "
                                             "/hooks/openhands/conversations"})
        ev = body
        try:
            code, resp = handle_event(ev)
        except Exception as e:              # noqa: BLE001 — server boundary: the
            # sender must get a response so its retry can re-deliver (unseen).
            return self._send(500, {"error": f"enqueue failed: {e}"})
        if resp.get("accepted") and AUTORUN:
            start_drain()
        self._send(code, resp)


if __name__ == "__main__":
    port = int(os.environ.get("AIQE_HOOK_PORT", "4998"))
    # Localhost by default; containers set AIQE_HOOK_HOST=0.0.0.0. Only expose behind
    # the token (AIQE_HOOK_TOKEN) and a Route/Ingress you control.
    host = os.environ.get("AIQE_HOOK_HOST", "127.0.0.1")
    # flush=True, and the warning below goes to stderr. Python block-buffers
    # stdout when it is a pipe or a file, so `make hook-server > log` and any
    # supervisor that captures output showed NOTHING at startup -- including
    # the no-token warning, which is the one line worth seeing immediately.
    # Measured: the same command under `python3 -u` printed both lines; without
    # it the log was 0 bytes. The container is unaffected (the Dockerfile sets
    # PYTHONUNBUFFERED=1), which is exactly why this stayed invisible.
    print(f"TaskEvent receiver: http://{host}:{port}/hooks/taskevent  "
          f"(auth: {'X-AIQE-Token required' if TOKEN else 'OFF - set AIQE_HOOK_TOKEN'}; "
          f"autorun: {'on' if AUTORUN else 'off'})", flush=True)
    # Auth OFF on loopback is a fine dev default; auth OFF on a routable
    # interface is the one combination that matters, and the line above said
    # exactly the same thing about both. State the difference where it applies:
    # anything that reaches this port can enqueue work with no credential.
    if not TOKEN and host not in ("127.0.0.1", "localhost", "::1"):
        # stderr: a warning belongs there, and it is not block-buffered, so it
        # survives being piped even if someone removes the flush above.
        print(f"  WARNING: listening on {host} with NO token -- every reachable "
              f"client can enqueue work. Set AIQE_HOOK_TOKEN before exposing it.",
              file=sys.stderr, flush=True)
    # A token that IS set but is the shipped placeholder cannot trip the check
    # above, so this port reads as authenticated while being protected by a
    # value published in this repository. deploy.sh warns when it falls back to
    # secret.example.yaml, but that is one line of deploy scrollback; this is
    # the log an operator looks at when they wonder whether it is safe.
    _ph = placeholder_secrets.warning(
        "AIQE_HOOK_TOKEN", TOKEN,
        "every reachable client can enqueue work.")
    if _ph:
        print(f"  {_ph}", file=sys.stderr, flush=True)
    ThreadingHTTPServer((host, port), Handler).serve_forever()
