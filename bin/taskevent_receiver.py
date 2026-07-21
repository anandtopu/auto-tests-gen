#!/usr/bin/env python3
"""TaskEvent receiver — the normalized webhook endpoint (architecture §5.10 rule 3).

Jira Automation rules, Bitbucket/Stash webhooks, and OpenHands conversation
starters all POST the same TaskEvent shape (triggers/task-event-schema.json):

  POST /hooks/taskevent   {"mode":"pr","repo":"orders-api","pr":201,"updated":"<sha>"}
                          {"mode":"jira","key":"PROJ-301","updated":"2026-07-21T10:00:00Z"}

Behavior: validate -> dedupe on sha256(mode|repo|pr|key|updated|workflow_version)
-> enqueue into the work queue (NFR-6: webhook redeliveries are no-ops). With
AIQE_HOOK_AUTORUN=1 a queue drain is started after each accepted event.

Auth: set AIQE_HOOK_TOKEN and senders must include header X-AIQE-Token.
Start: make hook-server   (default 127.0.0.1:4998, AIQE_HOOK_PORT to change)
"""
import hashlib, json, os, pathlib, subprocess, sys, threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine/lib"))
import fs_lock, work_queue

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


def seen_before(digest):
    """Record-and-check under lock; keeps a bounded window of recent digests."""
    with fs_lock.lock(SEEN_FILE):
        seen = []
        if SEEN_FILE.exists():
            try:
                seen = json.load(open(SEEN_FILE, encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                seen = []
        if digest in seen:
            return True
        seen = (seen + [digest])[-SEEN_MAX:]
        SEEN_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(SEEN_FILE, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(seen, fh)
        return False


def handle_event(ev):
    """Pure handler: returns (http_code, response_dict). Unit-testable."""
    err = validate(ev)
    if err:
        return 400, {"error": err}
    digest = idempotency_key(ev)
    if seen_before(digest):
        return 200, {"accepted": False, "reason": "duplicate delivery (idempotent no-op)",
                     "idempotency_key": digest[:16]}
    if ev["mode"] == "pr":
        item, fresh = work_queue.add("pr", ev["repo"], str(ev["pr"]),
                                     requested_by="taskevent")
    else:
        item, fresh = work_queue.add("jira", ev["key"], requested_by="taskevent")
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

    def do_POST(self):
        if TOKEN and self.headers.get("X-AIQE-Token", "") != TOKEN:
            return self._send(401, {"error": "missing or wrong X-AIQE-Token"})
        if self.path != "/hooks/taskevent":
            return self._send(404, {"error": "POST /hooks/taskevent"})
        try:
            ev = json.loads(self.rfile.read(
                int(self.headers.get("Content-Length", 0) or 0)) or b"{}")
        except json.JSONDecodeError as e:
            return self._send(400, {"error": f"invalid JSON: {e}"})
        code, resp = handle_event(ev)
        if resp.get("accepted") and AUTORUN:
            start_drain()
        self._send(code, resp)


if __name__ == "__main__":
    port = int(os.environ.get("AIQE_HOOK_PORT", "4998"))
    print(f"TaskEvent receiver: http://localhost:{port}/hooks/taskevent  "
          f"(auth: {'X-AIQE-Token required' if TOKEN else 'OFF - set AIQE_HOOK_TOKEN'}; "
          f"autorun: {'on' if AUTORUN else 'off'})")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
