#!/usr/bin/env python3
"""TaskEvent receiver — the normalized webhook endpoint (architecture §5.10 rule 3).

Jira Automation rules, Bitbucket/Stash webhooks, and OpenHands conversation
starters all POST the same TaskEvent shape (triggers/task-event-schema.json):

  POST /hooks/taskevent   {"mode":"pr","repo":"orders-api","pr":201,"updated":"<sha>"}
                          {"mode":"jira","key":"PROJ-301","updated":"2026-07-21T10:00:00Z"}

Behavior: validate -> dedupe on sha256(mode|repo|pr|key|updated|workflow_version)
-> enqueue into the work queue (NFR-6: webhook redeliveries are no-ops). With
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
import hashlib, json, os, pathlib, subprocess, sys, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine/lib"))
import fs_lock, openhands_events, work_queue

TOKEN = os.environ.get("AIQE_HOOK_TOKEN", "")
AUTORUN = os.environ.get("AIQE_HOOK_AUTORUN", "0") == "1"
SEEN_FILE = pathlib.Path(os.environ.get("AIQE_HOOKS_SEEN",
                                        ROOT / "reports/runs/hooks-seen.json"))
SEEN_MAX = 500
drain_lock = threading.Lock()


def validate(ev):
    mode = ev.get("mode")
    if mode == "pr":
        if not ev.get("repo") or not ev.get("pr"):
            return "pr mode requires repo and pr"
    elif mode == "jira":
        if not ev.get("key"):
            return "jira mode requires key"
    else:
        return "mode must be pr|jira"
    return None


def idempotency_key(ev):
    parts = [ev.get("mode", ""), ev.get("repo", ""), str(ev.get("pr", "")),
             ev.get("key", ""), ev.get("updated", ""), ev.get("workflow_version", "1")]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def _load_seen():
    if SEEN_FILE.exists():
        try:
            return json.load(open(SEEN_FILE, encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return []


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
            SEEN_FILE.parent.mkdir(parents=True, exist_ok=True)
            with open(SEEN_FILE, "w", encoding="utf-8", newline="\n") as fh:
                json.dump(seen, fh)


def handle_event(ev):
    """Pure handler: returns (http_code, response_dict). Unit-testable."""
    err = validate(ev)
    if err:
        return 400, {"error": err}
    digest = idempotency_key(ev)
    if already_seen(digest):
        return 200, {"accepted": False, "reason": "duplicate delivery (idempotent no-op)",
                     "idempotency_key": digest[:16]}
    if ev["mode"] == "pr":
        item, fresh = work_queue.add("pr", ev["repo"], str(ev["pr"]),
                                     requested_by="taskevent")
    else:
        item, fresh = work_queue.add("jira", ev["key"], requested_by="taskevent")
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
        try:
            body = json.loads(self.rfile.read(
                int(self.headers.get("Content-Length", 0) or 0)) or b"{}")
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
    print(f"TaskEvent receiver: http://{host}:{port}/hooks/taskevent  "
          f"(auth: {'X-AIQE-Token required' if TOKEN else 'OFF - set AIQE_HOOK_TOKEN'}; "
          f"autorun: {'on' if AUTORUN else 'off'})")
    ThreadingHTTPServer((host, port), Handler).serve_forever()
