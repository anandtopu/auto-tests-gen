#!/usr/bin/env python3
"""Interactive dashboard server (stdlib only): serves the QA dashboard with live
fetch-and-queue — pick a release, fetch its JIRA tickets and PRs, queue items, and
run the queue. Start: make serve  (default http://localhost:4999, AIQE_UI_PORT to change).

Endpoints:
  GET  /                      regenerate + serve the dashboard
  GET  /runs/<file>           run diffs (reports/runs/)
  GET  /<key>.log             gate logs (reports/)
  GET  /api/items?release=X   JIRA tickets (tracker search_release) + known PRs
  GET  /api/queue             queue contents
  GET  /api/export/plan?key=K&format=md|html|docx|pdf   download the ticket's test plan
  POST /api/export/confluence {"key","space"?,"title"?}  publish the plan to Confluence
  POST /api/queue             {"mode","target","pr","release"} -> enqueue
  POST /api/queue/run         drain the queue in a background process
  POST /api/queue/requeue     {"id"} -> put a failed item back in the queue
  POST /api/queue/remove      {"id"} -> delete a non-running item
"""
import glob, json, os, pathlib, re, subprocess, sys, threading, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine/lib"))
import export_plan, review_state, work_queue

MOCK = os.environ.get("AIQE_MOCK", "1") == "1"
TRACKER = ROOT / ("adapters/mock/tracker.sh" if MOCK else "adapters/tracker/jira.sh")
run_lock = threading.Lock()


def jira_items(release):
    r = subprocess.run([work_queue.bash_exe(), str(TRACKER), "search_release", release],
                       cwd=ROOT, capture_output=True, text=True, stdin=subprocess.DEVNULL)
    try:
        tickets = json.loads(r.stdout.strip().splitlines()[-1])
    except (json.JSONDecodeError, IndexError):
        tickets = []
    return [{"mode": "jira", "target": t["key"], "pr": None, "key": t["key"],
             "summary": t.get("summary", ""),
             "release": ",".join(t.get("fix_versions", []))} for t in tickets]


def pr_items(release):
    """Known PRs: benchmark fixtures + previously-run PR keys; release from the store."""
    reviews = review_state.load()
    seen, out = set(), []
    fixtures = glob.glob(str(ROOT / "eval/benchmark/prs/.changed-*.txt"))
    keys = [re.fullmatch(r"\.changed-(.+)-(\d+)\.txt", pathlib.Path(f).name)
            for f in fixtures]
    pairs = [(m.group(1), m.group(2)) for m in keys if m]
    for e in review_state.load():
        m = re.fullmatch(r"PR-(.+)-(\d+)", e)
        if m:
            pairs.append((m.group(1), m.group(2)))
    for repo, pr in pairs:
        key = f"PR-{repo}-{pr}"
        if key in seen:
            continue
        seen.add(key)
        rel = reviews.get(key, {}).get("release", "")
        if release and rel != release:
            continue
        out.append({"mode": "pr", "target": repo, "pr": pr, "key": key,
                    "summary": f"pull request #{pr} on {repo}", "release": rel})
    return out


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *a):                       # quiet request log
        pass

    def do_GET(self):
        url = urllib.parse.urlparse(self.path)
        if url.path in ("/", "/dashboard.html"):
            subprocess.run([sys.executable, str(ROOT / "bin/dashboard.py")],
                           cwd=ROOT, capture_output=True, stdin=subprocess.DEVNULL)
            self._send(200, (ROOT / "reports/dashboard.html").read_bytes(),
                       "text/html; charset=utf-8")
        elif url.path == "/api/items":
            rel = urllib.parse.parse_qs(url.query).get("release", [""])[0]
            queued = {work_queue.key_of(i) for i in work_queue.load()
                      if i["status"] in ("queued", "running")}
            items = jira_items(rel) + pr_items(rel)
            for i in items:
                i["queued"] = i["key"] in queued
            self._send(200, items)
        elif url.path == "/api/queue":
            self._send(200, work_queue.load())
        elif url.path == "/api/export/plan":
            q = urllib.parse.parse_qs(url.query)
            key = q.get("key", [""])[0]
            fmt = q.get("format", ["md"])[0]
            if fmt not in export_plan.FORMATS or not re.fullmatch(r"[\w.-]+", key or ""):
                self._send(400, {"error": f"key and format={'|'.join(export_plan.FORMATS)} required"})
                return
            try:
                content, ctype = export_plan.render(key, fmt)
            except SystemExit as e:                     # no plan for this key
                self._send(404, {"error": str(e)})
                return
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Disposition",
                             f'attachment; filename="{key}-testplan.{fmt}"')
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
        elif url.path.startswith("/runs/"):
            f = ROOT / "reports/runs" / pathlib.PurePosixPath(url.path).name
            self._send(200, f.read_bytes(), "text/plain; charset=utf-8") \
                if f.exists() else self._send(404, {"error": "not found"})
        elif url.path.endswith(".log"):
            f = ROOT / "reports" / pathlib.PurePosixPath(url.path).name
            self._send(200, f.read_bytes(), "text/plain; charset=utf-8") \
                if f.exists() else self._send(404, {"error": "not found"})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)) or 0)
        if self.path == "/api/queue":
            try:
                p = json.loads(body or b"{}")
                item, fresh = work_queue.add(p["mode"], p["target"], p.get("pr"),
                                             p.get("release", ""), "dashboard")
                self._send(200, {"queued": fresh, "item": item})
            except (KeyError, json.JSONDecodeError, SystemExit) as e:
                self._send(400, {"error": str(e)})
        elif self.path == "/api/export/confluence":
            try:
                p = json.loads(body or b"{}")
                result = export_plan.publish_to_confluence(
                    p["key"], p.get("space"), p.get("title"))
                self._send(200, {"ok": True, "result": result})
            except (KeyError, json.JSONDecodeError) as e:
                self._send(400, {"error": str(e)})
            except SystemExit as e:                     # no plan / publish failure
                self._send(409, {"error": str(e)})
        elif self.path in ("/api/queue/requeue", "/api/queue/remove"):
            try:
                item_id = json.loads(body or b"{}")["id"]
                fn = work_queue.requeue if self.path.endswith("requeue") else work_queue.remove
                self._send(200, {"ok": True, "item": fn(item_id)})
            except (KeyError, json.JSONDecodeError) as e:
                self._send(400, {"error": str(e)})
            except SystemExit as e:          # library rejections (wrong status, unknown id)
                self._send(409, {"error": str(e)})
        elif self.path == "/api/queue/run":
            if run_lock.locked():
                self._send(409, {"error": "queue is already running"})
                return
            def drain():
                with run_lock:
                    subprocess.run([sys.executable, str(ROOT / "engine/lib/work_queue.py"),
                                    "run"], cwd=ROOT, stdin=subprocess.DEVNULL)
            threading.Thread(target=drain, daemon=True).start()
            self._send(200, {"started": True})
        else:
            self._send(404, {"error": "not found"})


if __name__ == "__main__":
    port = int(os.environ.get("AIQE_UI_PORT", "4999"))
    print(f"AI QE dashboard: http://localhost:{port}  "
          f"(mode: {'mock' if MOCK else 'real'} adapters; Ctrl-C to stop)")
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
