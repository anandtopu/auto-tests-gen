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
  GET  /api/report?days=N&release=X&format=md|html|docx|pdf   team status report
  POST /api/email/report      {"days"?,"release"?,"to"?}  email the team report
  POST /api/email/run         {"run_id","to"?}            email a run's gate summary
  POST /api/email/digest      {"to"?}                     email the pending-review digest
  POST /api/export/confluence {"key","space"?,"title"?}  publish the plan to Confluence
  POST /api/export/attach     {"key","format"?}          attach the plan to the JIRA ticket
  POST /api/review            {"key","status","by"?,"note"?}  set team-review status
                              (the dashboard's Approve button; statuses per review_state.VALID)
  POST /api/queue             {"mode","target","pr","release"} -> enqueue
  POST /api/queue/inline      {"text","key"?,"components"?,"labels"?,"repos"?,"type"?}
                              -> synthesize a ticket from pasted JIRA context + enqueue
  POST /api/queue/run         drain the queue in a background process
  POST /api/queue/requeue     {"id"} -> put a failed item back in the queue
  POST /api/queue/remove      {"id"} -> delete a non-running item
  GET  /api/plans             test plans + lifecycle status
  GET  /api/plans/one?key=K   one plan's markdown + status
  POST /api/plans/save        {"key","text","by"?}   edit (resets an approved plan)
  POST /api/plans/status      {"key","status","by"?,"note"?}  review/approve/changes
  POST /api/plans/link        {"key","format"?}      attach the approved plan to JIRA
  POST /api/plans/generate    {"key"}                queue test generation (needs approval)
  GET  /api/repos             estate summary (app repos, test repos, scope, guidance)
  POST /api/repos/app         add/edit an app repo (repo_admin.upsert_app fields)
  POST /api/repos/test        add/edit a test repo (repo_admin.upsert_test fields)
  POST /api/repos/scope       {"test_repo","apps"} -> declared mapping; covers regen
  POST /api/repos/remove      {"name","section":"app"|"test","force"?}
  GET  /api/repos/sync        per-repo guidance sync status (AGENTS.md/CLAUDE.md)
  POST /api/repos/sync        {"repo"?}  pull guidance from the SCM (all repos when
                              omitted) and regenerate AGENTS.md
  GET  /api/repos/notes?repo=R    per-repo agent guidance (+ repo-local files)
  POST /api/repos/notes       {"repo","text"} -> knowledge/repos/<R>.md + AGENTS.md
  POST /api/integrations/check  {"which"?: [...]}  read-only connectivity check of
                              every configured external system (nothing is posted,
                              pushed or sent)
  GET  /api/settings          integration settings (secrets masked to set/unset)
  POST /api/settings          {"updates": {ENV: value}} -> merge into .env
  POST /api/demo/clear        delete generated demo data (run history, plans,
                              exports, scratch; estate registry/catalog kept)
"""
import glob, json, os, pathlib, re, subprocess, sys, threading, urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "engine/lib"))
import demo_data, email_notify, export_plan, guidance_sync, inline_ticket, \
    integration_check, plan_state, repo_admin, review_state, settings_store, \
    team_report, work_queue

# The Settings view writes .env; honor it here too (explicit env still wins) so
# adapter mode and credentials configured in the UI actually reach this server.
settings_store.load_env_into()
MOCK = os.environ.get("AIQE_MOCK", "1") == "1"
TRACKER = ROOT / ("adapters/mock/tracker.sh" if MOCK else "adapters/tracker/jira.sh")
UI_TOKEN = os.environ.get("AIQE_UI_TOKEN", "")   # empty = auth off (localhost-only dev)
run_lock = threading.Lock()


def jira_items(release):
    r = subprocess.run([work_queue.bash_exe(), str(TRACKER), "search_release", release],
                       cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace",
                       stdin=subprocess.DEVNULL)
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
        if getattr(self, "_set_cookie", False):    # token arrived via ?token= — persist it
            self.send_header("Set-Cookie", f"aiqe_token={UI_TOKEN}; HttpOnly; SameSite=Strict")
            self._set_cookie = False
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *a):                       # quiet request log
        pass

    def _authed(self):
        """True when AIQE_UI_TOKEN is unset, or the request carries it (query param
        on first visit -> cookie; Authorization: Bearer for API clients)."""
        if not UI_TOKEN:
            return True
        q = urllib.parse.parse_qs(urllib.parse.urlparse(self.path).query)
        self._set_cookie = q.get("token", [""])[0] == UI_TOKEN
        if self._set_cookie:
            return True
        if self.headers.get("Authorization", "") == f"Bearer {UI_TOKEN}":
            return True
        cookies = self.headers.get("Cookie", "")
        return f"aiqe_token={UI_TOKEN}" in cookies.replace(" ", "").split(";")

    def _deny(self):
        self._send(401, {"error": "unauthorized: open /?token=<AIQE_UI_TOKEN> "
                                  "or send Authorization: Bearer <token>"})

    def do_GET(self):
        if not self._authed():
            return self._deny()
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
        elif url.path == "/api/plans":
            self._send(200, plan_state.summary())
        elif url.path == "/api/plans/one":
            key = urllib.parse.parse_qs(url.query).get("key", [""])[0]
            p = plan_state.plan_path(key) if re.fullmatch(r"[\w.-]+", key or "") else None
            if not p or not p.exists():
                self._send(404, {"error": f"no test plan for {key}"})
            else:
                self._send(200, {"key": key, "text": p.read_text(encoding="utf-8"),
                                 **plan_state.get(key)})
        elif url.path == "/api/repos":
            self._send(200, repo_admin.summary())
        elif url.path == "/api/repos/sync":
            self._send(200, guidance_sync.status())
        elif url.path == "/api/repos/notes":
            repo = urllib.parse.parse_qs(url.query).get("repo", [""])[0]
            try:
                self._send(200, repo_admin.get_notes(repo))
            except SystemExit as e:
                self._send(404, {"error": str(e)})
        elif url.path == "/api/settings":
            self._send(200, settings_store.get_settings())
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
        elif url.path == "/api/report":
            q = urllib.parse.parse_qs(url.query)
            fmt = q.get("format", ["md"])[0]
            days = q.get("days", [""])[0]
            release = q.get("release", [""])[0]
            if fmt not in team_report.FORMATS or (days and not days.isdigit()) \
                    or (release and not re.fullmatch(r"[\w.-]+", release)):
                self._send(400, {"error": "format=md|html|docx|pdf; days must be a "
                                          "number; release must be a version string"})
                return
            content, ctype = team_report.render(fmt, int(days) if days else None,
                                                release or None)
            name = "team-report" + (f"-{release}" if release else "") + f".{fmt}"
            self.send_response(200)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Disposition", f'attachment; filename="{name}"')
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
        if not self._authed():
            return self._deny()
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)) or 0)
        if self.path == "/api/queue":
            try:
                p = json.loads(body or b"{}")
                item, fresh = work_queue.add(p["mode"], p["target"], p.get("pr"),
                                             p.get("release", ""), "dashboard")
                self._send(200, {"queued": fresh, "item": item})
            except (KeyError, json.JSONDecodeError, SystemExit) as e:
                self._send(400, {"error": str(e)})
        elif self.path == "/api/queue/inline":
            try:
                p = json.loads(body or b"{}")
                csv_ = lambda s: [v.strip() for v in (s or "").split(",") if v.strip()]
                ticket = inline_ticket.build(p["text"], p.get("key") or None,
                                             csv_(p.get("components")), csv_(p.get("labels")),
                                             csv_(p.get("repos")), p.get("type") or "Story")
                path = inline_ticket.write(ticket)
                item, fresh = work_queue.add("jira", ticket["key"], release="",
                                             requested_by="dashboard-inline",
                                             inline_file=path)
                self._send(200, {"queued": fresh, "key": ticket["key"], "item": item})
            except (KeyError, json.JSONDecodeError, ValueError, SystemExit) as e:
                self._send(400, {"error": str(e)})
        elif self.path == "/api/review":
            try:
                p = json.loads(body or b"{}")
                entry = review_state.set_status(p["key"], p["status"],
                                                p.get("by", "dashboard"), p.get("note", ""))
                self._send(200, {"ok": True, "key": p["key"], "status": entry["status"]})
            except (KeyError, json.JSONDecodeError) as e:
                self._send(400, {"error": str(e)})
            except SystemExit as e:                     # invalid status
                self._send(400, {"error": str(e)})
        elif self.path in ("/api/export/confluence", "/api/export/attach"):
            try:
                p = json.loads(body or b"{}")
                if self.path.endswith("confluence"):
                    result = export_plan.publish_to_confluence(
                        p["key"], p.get("space"), p.get("title"))
                else:
                    result = export_plan.attach_to_jira(p["key"], p.get("format", "pdf"))
                self._send(200, {"ok": True, "result": result})
            except (KeyError, json.JSONDecodeError) as e:
                self._send(400, {"error": str(e)})
            except SystemExit as e:                     # no plan / publish or attach failure
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
        elif self.path.startswith("/api/repos/"):
            try:
                p = json.loads(body or b"{}")
                if self.path == "/api/repos/app":
                    result = repo_admin.upsert_app(
                        p["name"], kind=p.get("kind"), scm=p.get("scm"),
                        url=p.get("url"), domains=p.get("domains"),
                        testable_paths=p.get("testable_paths"),
                        contract=p.get("contract"), route_table=p.get("route_table"),
                        consumes_services=p.get("consumes_services"))
                elif self.path == "/api/repos/test":
                    result = repo_admin.upsert_test(
                        p["name"], layer=p.get("layer"), framework=p.get("framework"),
                        scm=p.get("scm"), url=p.get("url"), specs=p.get("specs"),
                        fixtures=p.get("fixtures"), scope=p.get("scope"))
                elif self.path == "/api/repos/scope":
                    result = repo_admin.set_scope(p["test_repo"], p.get("apps", ""))
                elif self.path == "/api/repos/remove":
                    fn = (repo_admin.remove_test if p.get("section") == "test"
                          else repo_admin.remove_app)
                    result = fn(p["name"], force=bool(p.get("force")))
                elif self.path == "/api/repos/notes":
                    result = repo_admin.set_notes(p["repo"], p.get("text", ""))
                elif self.path == "/api/repos/sync":
                    # Pull AGENTS.md/CLAUDE.md from the SCM, then refresh the estate
                    # knowledge so the next generation run uses the latest guidance.
                    repo = p.get("repo")
                    result = (guidance_sync.sync_repo(repo, p.get("ref"))
                              if repo else guidance_sync.sync_all(p.get("ref")))
                    guidance_sync.regenerate_agents_md()
                else:
                    self._send(404, {"error": "not found"})
                    return
                self._send(200, {"ok": True, **result})
            except (KeyError, json.JSONDecodeError) as e:
                self._send(400, {"error": str(e)})
            except SystemExit as e:                     # validation failures
                self._send(400, {"error": str(e)})
        elif self.path == "/api/settings":
            try:
                p = json.loads(body or b"{}")
                self._send(200, {"ok": True, **settings_store.save(p["updates"])})
            except (KeyError, json.JSONDecodeError) as e:
                self._send(400, {"error": str(e)})
            except SystemExit as e:                     # unknown key / bad value
                self._send(400, {"error": str(e)})
        elif self.path.startswith("/api/plans/"):
            try:
                p = json.loads(body or b"{}")
                key = p["key"]
                if self.path.endswith("/save"):
                    result = plan_state.save_plan(key, p.get("text", ""),
                                                  p.get("by", "dashboard"))
                elif self.path.endswith("/status"):
                    result = plan_state.set_status(key, p["status"],
                                                   p.get("by", "dashboard"),
                                                   p.get("note", ""))
                elif self.path.endswith("/link"):
                    plan_state.require_approved(key)
                    ref = export_plan.attach_to_jira(key, p.get("format", "pdf"))
                    result = plan_state.mark_linked(key, ref, p.get("by", "dashboard"))
                    result = {**result, "ref": ref}
                elif self.path.endswith("/generate"):
                    plan_state.require_approved(key)   # fail fast before queueing
                    item, fresh = work_queue.add("tests", key, release="",
                                                 requested_by="dashboard-plan")
                    result = {"queued": fresh, "item": item}
                else:
                    self._send(404, {"error": "not found"}); return
                self._send(200, {"ok": True, **result})
            except (KeyError, json.JSONDecodeError) as e:
                self._send(400, {"error": str(e)})
            except SystemExit as e:            # not approved / no plan / bad status
                self._send(409, {"error": str(e)})
        elif self.path.startswith("/api/email/"):
            try:
                p = json.loads(body or b"{}")
                to = p.get("to") or None
                if self.path.endswith("/report"):
                    days = p.get("days")
                    subj, text, html = email_notify.team_report_email(
                        int(days) if days else None, p.get("release") or None)
                elif self.path.endswith("/run"):
                    subj, text, html = email_notify.run_summary(p["run_id"])
                elif self.path.endswith("/digest"):
                    subj, text, html = email_notify.review_digest()
                else:
                    self._send(404, {"error": "not found"}); return
                self._send(200, {"ok": True, "result": email_notify.send(subj, text, html, to)})
            except (KeyError, json.JSONDecodeError) as e:
                self._send(400, {"error": str(e)})
            except SystemExit as e:                     # no recipients / no run record
                self._send(400, {"error": str(e)})
            except Exception as e:                      # SMTP failure — report, don't crash
                self._send(502, {"error": f"email failed: {e}"})
        elif self.path == "/api/integrations/check":
            try:
                p = json.loads(body or b"{}")
                self._send(200, integration_check.run(p.get("which")))
            except json.JSONDecodeError as e:
                self._send(400, {"error": str(e)})
        elif self.path == "/api/demo/clear":
            try:
                self._send(200, {"ok": True, **demo_data.clear()})
            except SystemExit as e:                     # pipeline run in progress
                self._send(409, {"error": str(e)})
            except OSError as e:                        # locked/undeletable file
                self._send(500, {"error": f"clear failed: {e}"})
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
    # Bind localhost by default; containers set AIQE_UI_HOST=0.0.0.0 to be reachable
    # from the Service. Expose only behind the token auth (AIQE_UI_TOKEN) + a Route/
    # Ingress you control — never 0.0.0.0 without a token on an untrusted network.
    host = os.environ.get("AIQE_UI_HOST", "127.0.0.1")
    print(f"AI QE dashboard: http://{host}:{port}  "
          f"(mode: {'mock' if MOCK else 'real'} adapters; Ctrl-C to stop)")
    ThreadingHTTPServer((host, port), Handler).serve_forever()
