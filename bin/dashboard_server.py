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
  GET  /api/openhands         live OpenHands agent conversations (webhook-fed)
  GET  /api/plans             test plans + lifecycle status
  GET  /api/plans/one?key=K   one plan's markdown + status
  POST /api/plans/save        {"key","text","by"?}   edit (resets an approved plan)
  POST /api/plans/status      {"key","status","by"?,"note"?}  review/approve/changes
  POST /api/plans/link        {"key","format"?}      attach the approved plan to JIRA
  POST /api/plans/comment     {"key"}  post the plan+tests linking comment on the ticket
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

# Bumped whenever a UI action's server contract changes. The page (rendered fresh per
# request) compares this against its own copy: a mismatch means the SERVER process
# predates the code on disk — the exact condition that made "Clear demo data" run an
# old target list while the page promised the new one. Restarting make serve fixes it.
UI_SCHEMA = 2

# Reverse-proxy SSO (product-direction H1): the header name a trusted proxy sets
# with the authenticated user, e.g. X-Forwarded-User. Empty = SSO off.
SSO_HEADER = os.environ.get("AIQE_SSO_HEADER", "").strip()
sys.path.insert(0, str(ROOT / "engine/lib"))
import alert_rules, demo_data, email_notify, event_log, export_plan, \
    guidance_sync, inline_ticket, integration_check, openhands_client, \
    openhands_events, openhands_mode, plan_state, pr_url, repo_admin, \
    repo_guidance_gen, review_state, settings_store, spec_workflow, \
    team_report, waiver_store, work_queue
import governance_page
import spec_savings
import env_flag                     # AIQE_MOCK means what it says


def _json_flag(value, unusable):
    """A JSON request flag, resolved strictly.

    `p.get("factory")` used Python truthiness, so the STRING "false" — or "no",
    or any other spelling a caller might send meaning the opposite — is truthy
    and would have triggered a factory reset that empties the repo registry and
    team notes. `dry` happens to fail safe under truthiness (any non-empty
    string means "preview"), which is exactly why the inconsistency survived:
    the harmless case looked like proof the pattern was fine.

    `unusable` is the answer for a value we cannot read, and it differs per
    flag: False for destructive ones (do not destroy on a value we do not
    understand) and True for `dry` (prefer the preview). Same rule as C13's
    knobs — resolve toward the outcome you can recover from.
    """
    if isinstance(value, bool):
        return value
    if value is None:
        return False                       # absent means "not asked for"
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return bool(value)
    if isinstance(value, str):
        v = value.strip().lower()
        if v in ("1", "true", "yes", "on"):
            return True
        if v in ("0", "false", "no", "off", ""):
            return False
    return unusable


def _classify_status(code):
    """HTTP status -> (event kind, outcome).

    Extracted from the request handler so it can be tested without a socket —
    the first live run classified a 400 as `ok` because only 401/403 were
    special-cased, which would have made "nothing is failing" true of a log
    full of rejected requests.

    Any 4xx is a REFUSAL: the transaction did not happen. A missing status
    (the handler raised before responding) is a FAILURE, not an unknown —
    something went wrong and the client got nothing back.
    """
    if code is None or code >= 500:
        return "request.failed", "failed"
    if code >= 400:
        return "request.refused", "refused"
    return "request.received", "ok"


def _csv_cell(v):
    """One CSV field, quoted defensively.

    A `target` is an endpoint path or a ticket key, and a `kind` is from a
    closed vocabulary — but an actor arrives from an SSO header we do not
    control. A value starting with = + - @ is treated as a FORMULA by Excel and
    Sheets, so it is prefixed with a quote: an export that runs code when
    opened is a real attack on the person doing the audit, not a theoretical
    one. Values are already redacted at write time; this is about the reader.
    """
    if v is None:
        return ""
    s = str(v)
    if s[:1] in ("=", "+", "-", "@", "\t", "\r"):
        s = "'" + s
    if any(c in s for c in (",", '"', "\n", "\r")):
        s = '"' + s.replace('"', '""') + '"'
    return s

# The Settings view writes .env; honor it here too (explicit env still wins) so
# adapter mode and credentials configured in the UI actually reach this server.
settings_store.load_env_into()
MOCK = env_flag.mock()
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


def _err(e):
    """Human-readable message for a handler exception. A bare KeyError renders as
    "'target'" — useless to whoever posted the payload; name the missing field."""
    if isinstance(e, KeyError):
        return f"missing field: {e.args[0] if e.args else '?'}"
    return str(e)


class _Server(ThreadingHTTPServer):
    """The dashboard's HTTP server, with a listen backlog that matches how the
    page actually behaves.

    `socketserver` defaults `request_queue_size` to 5. One dashboard page load
    fires roughly ten loaders at once (activity, alerts, spec workflow, savings,
    governance, trace, cost, plans, queue, conversations), so the sixth
    connection onward overflowed the accept queue and Windows answered with an
    RST — the client saw a connection reset after ~19s of retrying.

    The symptom was not an error anybody could read: the affected loader caught
    its own failure and left an EMPTY TABLE, so the Activity view — the
    transaction log — routinely rendered blank while the log was full. It looked
    like "no transactions", which is the opposite of the truth. Measured before
    the fix: 4 of 7 concurrent requests reset; after: 0 of 40.

    `daemon_threads` keeps Ctrl-C from hanging on an in-flight request, and
    `allow_reuse_address` avoids a TIME_WAIT bind failure on quick restarts —
    both routine for a dev-facing server that gets restarted constantly.
    """
    request_queue_size = 128
    daemon_threads = True

    # NOT `True` on Windows, whatever `HTTPServer` says. The stdlib base
    # `TCPServer` sets this False and `HTTPServer` flips it to True, which on
    # Linux only shortens TIME_WAIT — but on Windows SO_REUSEADDR lets a second
    # process bind an address that is already LISTENing. The second `make serve`
    # then appears to start, both processes hold :4999, and connections are
    # split between them non-deterministically.
    #
    # That failure is genuinely hard to read: half the requests are answered by
    # a server running whatever code was on disk when IT started. This session
    # lost time to it twice — a page served with an old column set, and API
    # routes 404ing that plainly existed — and misdiagnosed both as caching.
    # The existing UI_SCHEMA check exists for the same class of problem and
    # cannot help here, because the stale process answers the version probe too.
    #
    # With it off, the second start fails at bind, and `main` says so.
    allow_reuse_address = sys.platform != "win32"


class Handler(BaseHTTPRequestHandler):
    def _send(self, code, body, ctype="application/json"):
        # Remembered for the do_POST wrapper below, which needs the status to
        # classify the transaction. Recorded HERE because every branch of the
        # handler funnels through _send — so one line covers all 34 endpoints
        # and cannot drift as endpoints are added.
        self._last_status = code
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        # The dashboard is regenerated per request and the APIs are live state — a
        # heuristically-cached copy shows stale views/JS after every deploy or edit
        # (seen as: reload still serves the previous page). Nothing here is worth
        # caching, so say so explicitly.
        self.send_header("Cache-Control", "no-store")
        if getattr(self, "_set_cookie", False):    # token arrived via ?token= — persist it
            self.send_header("Set-Cookie", f"aiqe_token={UI_TOKEN}; HttpOnly; SameSite=Strict")
            self._set_cookie = False
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, fmt, *a):                       # quiet request log
        pass

    def _authed(self):
        """Authentication, two independent mechanisms:

        SSO header (AIQE_SSO_HEADER, e.g. X-Forwarded-User): set it ONLY when a
        reverse proxy (OpenShift oauth-proxy, nginx+SSO) terminates auth in front of
        this server and strips/overwrites the header on incoming traffic — the
        header is trusted verbatim, so a directly-reachable server with SSO enabled
        would be spoofable. When configured it FAILS CLOSED: no header -> 401, so a
        proxy misconfiguration can never silently expose the dashboard. The value
        becomes the actor for approvals/marks that don't name one explicitly.

        Token (AIQE_UI_TOKEN): unchanged — query param on first visit -> cookie, or
        Authorization: Bearer. With SSO on, a valid token still authenticates (CLI
        and health-check clients bypass the proxy), acting as "token-client".
        """
        self.user = ""
        if SSO_HEADER:
            ident = (self.headers.get(SSO_HEADER) or "").strip()
            if ident:
                self.user = ident
                return True
            # fall through: a Bearer token may still authenticate an API client
            if UI_TOKEN and self.headers.get("Authorization", "") == f"Bearer {UI_TOKEN}":
                self.user = "token-client"
                return True
            return False
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
        if SSO_HEADER:
            self._send(401, {"error": f"unauthorized: expected the SSO header "
                                      f"'{SSO_HEADER}' from the reverse proxy "
                                      "(or Authorization: Bearer <AIQE_UI_TOKEN>)"})
            return
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
        elif url.path == "/api/governance":
            # SDD adoption S6. Generated from the constitution + live config, so
            # it cannot drift from what the platform actually enforces.
            # `format=md` serves the shareable document.
            if urllib.parse.parse_qs(url.query).get("format", [""])[0] == "md":
                return self._send(200, governance_page.markdown().encode("utf-8"),
                                  "text/markdown; charset=utf-8")
            self._send(200, governance_page.page())
        elif url.path == "/api/waivers":
            # SDD adoption S4. Expired waivers are INCLUDED — a lapsed exception
            # is the most interesting row on the page, not one to hide.
            key = (urllib.parse.parse_qs(url.query).get("key", [""])[0] or "").strip()
            self._send(200, {"key": key,
                             "waivers": waiver_store.list_for(key) if key else [],
                             "attention": waiver_store.attention(),
                             "max_days": waiver_store.MAX_DAYS})
        elif url.path == "/api/requirements":
            # SDD adoption S2. EARS statements + ambiguities for one ticket.
            # This is the step most likely to be skipped because it was
            # CLI-only, and the one that prevents the expensive failure: a test
            # that faithfully encodes a misunderstanding.
            key = (urllib.parse.parse_qs(url.query).get("key", [""])[0] or "").strip()
            if not key:
                return self._send(400, {"error": "key required"})
            import spec_store
            doc = spec_store.load_requirements(key) or {}
            amb = spec_store.ambiguities(key) or []
            entry = plan_state.get(key) or {}
            # Flat fields on the entry — plan_state stores `requirements_status`
            # and `requirements_sha`, not a nested dict.
            signed = entry.get("requirements_sha") or ""
            # Compare LIKE WITH LIKE. The first version compared this against
            # spec_store.sha(key), which hashes specs/<KEY>/testplan.yaml with a
            # different (truncated) function — a different file AND a different
            # hash, so every approved requirement reported as stale. plan_state
            # signs sha256 of requirements.yaml; recompute exactly that.
            import hashlib
            rp = pathlib.Path(spec_store.requirements_path(key))
            current = (hashlib.sha256(rp.read_bytes()).hexdigest()
                       if rp.exists() else "")
            self._send(200, {
                "key": key,
                "requirements": doc.get("requirements") or [],
                "ambiguities": amb,
                "blocking": [a for a in amb if isinstance(a, dict) and a.get("blocking")],
                "status": entry.get("requirements_status") or "",
                "by": next((h.get("by", "") for h in reversed(entry.get("history") or [])
                            if h.get("requirements")), ""),
                "signed_sha": signed,
                "current_sha": current,
                # A signed sha that no longer matches means the file changed
                # AFTER approval. Silence here would let a stale approval look
                # live — the same trap plan approval already guards against.
                "stale": bool(signed and current and signed != current),
                "gate_on": spec_workflow.governance()["requirements_gate"],
            })
        elif url.path == "/api/spec-savings":
            # SDD adoption S5. Coverage subtraction: approved scenarios a
            # cataloged test already exercises need no authoring call. COUNTS
            # only — the money figure stays absent until a measured authoring
            # cost exists, because a savings number is exactly the kind of
            # figure people repeat in a status update.
            k = urllib.parse.parse_qs(url.query).get("key", [""])[0].strip()
            self._send(200, spec_savings.authoring_plan(k) if k
                       else spec_savings.estate())
        elif url.path == "/api/spec-workflow":
            # SDD adoption S1. Read-only by construction: rendering a workflow
            # view must never advance a workflow. Every transition stays behind
            # the approve/edit commands, which sign and record an actor.
            self._send(200, spec_workflow.board())
        elif url.path == "/api/alerts":
            # Rules plus their CURRENT evaluation (observability 3.1-3.4).
            # notify=False: rendering a page must never send a notification —
            # opening a dashboard is not an alerting event.
            doc = alert_rules.load()
            status = alert_rules.evaluate(notify=False)
            self._send(200, {"rules": doc.get("rules") or [], "status": status,
                             "kinds": sorted(event_log.KINDS),
                             "channels": list(alert_rules.CHANNELS)})
        elif url.path == "/api/events":
            # Activity view (observability 2.1-2.3). Filters map straight onto
            # event_log.read; `format=csv` serves the export. `corrupt` and the
            # log's own health ride along so the UI can say the history is
            # INCOMPLETE rather than presenting a convincing partial list —
            # the same rule the cost report follows about unmeasured figures.
            q = urllib.parse.parse_qs(url.query)
            one = lambda k: (q.get(k, [""])[0] or "").strip()   # noqa: E731
            kinds = [k for k in one("kind").split(",") if k]
            try:
                limit = max(1, min(2000, int(one("limit") or 200)))
            except ValueError:
                limit = 200
            rows, corrupt = event_log.read(
                limit=limit, kinds=kinds or None, actor=one("actor") or None,
                target=one("target") or None, outcome=one("outcome") or None,
                run_id=one("run_id") or None, since=one("since") or None)
            if one("format") == "csv":
                cols = ["ts", "kind", "actor", "actor_source", "source",
                        "target", "run_id", "outcome", "duration_ms"]
                out = [",".join(cols)]
                for r in rows:
                    out.append(",".join(_csv_cell(r.get(c)) for c in cols))
                return self._send(200, ("\n".join(out) + "\n").encode("utf-8"),
                                  "text/csv; charset=utf-8")
            self._send(200, {"events": rows, "corrupt": corrupt,
                             "health": event_log.health()})
        elif url.path == "/api/items":
            rel = urllib.parse.parse_qs(url.query).get("release", [""])[0]
            # Mode-aware: a pending PLAN-ONLY item must not mark the ticket's
            # full-run Queue button as already queued (and vice versa).
            pending = [i for i in work_queue.load()
                       if i["status"] in ("queued", "running")]
            queued = {(i["mode"], work_queue.key_of(i)) for i in pending}
            items = jira_items(rel) + pr_items(rel)
            for i in items:
                i["queued"] = (i["mode"], i["key"]) in queued
                i["plan_queued"] = ("plan", i["key"]) in queued
            self._send(200, items)
        elif url.path == "/api/queue":
            self._send(200, work_queue.load())
        elif url.path == "/api/openhands":
            self._send(200, openhands_events.summary())
        elif url.path == "/api/openhands/health":
            # Re-load .env so a key saved in Settings is visible without a restart.
            settings_store.load_env_into()
            self._send(200, openhands_client.health())
        elif url.path.startswith("/api/openhands/"):
            conv_id = url.path[len("/api/openhands/"):]
            if not re.fullmatch(r"[\w-]+", conv_id):
                self._send(400, {"error": "invalid conversation id"})
                return
            try:
                settings_store.load_env_into()
                self._send(200, openhands_client.status(conv_id))
            except RuntimeError as e:
                self._send(502, {"error": _err(e)})
        elif url.path == "/api/plans":
            self._send(200, plan_state.summary())
        elif url.path == "/api/plans/one":
            key = urllib.parse.parse_qs(url.query).get("key", [""])[0]
            p = plan_state.plan_path(key) if re.fullmatch(r"[\w.-]+", key or "") else None
            if not p or not p.exists():
                self._send(404, {"error": f"no test plan for {key}"})
            else:
                # SDD 2.1: requirement ambiguities ride along so the plan
                # reviewer sees WHAT the ticket left undefined beside the
                # scenarios that had to route around it.
                import spec_store
                self._send(200, {"key": key, "text": p.read_text(encoding="utf-8"),
                                 "ambiguities": spec_store.ambiguities(key),
                                 "spec": spec_store.load(key),
                                 "waivers": spec_store.load_waivers(key),
                                 **plan_state.get(key)})
        elif url.path == "/api/repos":
            self._send(200, repo_admin.summary())
        elif url.path == "/api/repos/sync":
            # Optional ?repo= filter. An unknown name is an error, not a silently
            # ignored parameter returning everything.
            repo = urllib.parse.parse_qs(url.query).get("repo", [""])[0]
            rows = guidance_sync.status()
            if repo:
                rows = [r for r in rows if r.get("name") == repo]
                if not rows:
                    self._send(404, {"error": f"not a registered repo: {repo}"})
                    return
            self._send(200, rows)
        elif url.path == "/api/repos/notes":
            repo = urllib.parse.parse_qs(url.query).get("repo", [""])[0]
            try:
                self._send(200, repo_admin.get_notes(repo))
            except SystemExit as e:
                self._send(404, {"error": _err(e)})
        elif url.path == "/api/repos/curated":
            # Durable, user-editable per-repo guidance + a generated draft to
            # start from (never auto-persisted — the user saves what they own).
            import curated_guidance, repo_guidance_gen as rgg
            q = urllib.parse.parse_qs(url.query)
            repo = q.get("repo", [""])[0]
            try:
                files = curated_guidance.get(repo)
            except SystemExit as e:
                self._send(404, {"error": _err(e)})
                return
            gen = rgg.generated_path(repo)
            self._send(200, {"repo": repo, "files": files,
                             "generated": gen.read_text(encoding="utf-8",
                                                        errors="replace")
                             if gen.exists() else "",
                             "effective": [f["path"] for f in
                                           repo_admin.repo_local_files(repo)]})
        elif url.path == "/api/repos/curated/export":
            import curated_guidance
            q = urllib.parse.parse_qs(url.query)
            repo, fn = q.get("repo", [""])[0], q.get("file", ["AGENTS.md"])[0]
            try:
                content = curated_guidance.get(repo).get(fn)
            except SystemExit as e:
                self._send(404, {"error": _err(e)})
                return
            if content is None:
                self._send(404, {"error": f"no curated {fn} for {repo}"})
                return
            data = content.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/markdown; charset=utf-8")
            self.send_header("Content-Disposition",
                             f'attachment; filename="{repo}-{fn}"')
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)
        elif url.path == "/api/version":
            self._send(200, {"ui_schema": UI_SCHEMA, "user": getattr(self, "user", ""),
                             "sso": bool(SSO_HEADER)})
        elif url.path == "/api/plans/diff-since-approval":
            # Roadmap 4.2: exactly what changed vs the text the approver signed —
            # empty when nothing changed or nothing was ever approved.
            dkey = urllib.parse.parse_qs(url.query).get("key", [""])[0]
            if not re.fullmatch(r"[\w.-]+", dkey or ""):
                self._send(400, {"error": "key required"})
            else:
                self._send(200, {"key": dkey,
                                 "diff": plan_state.diff_since_approval(dkey)})
        elif url.path == "/api/plans/similar":
            # Similar-plan retrieval (roadmap 6.1): SUGGESTIONS only — the human
            # sees the prior plan and its similarity; nothing is ever auto-applied.
            import plan_similarity
            skey = urllib.parse.parse_qs(url.query).get("key", [""])[0]
            if not re.fullmatch(r"[\w.-]+", skey or ""):
                self._send(400, {"error": "key required"})
            else:
                self._send(200, {"similar": plan_similarity.suggest_for(skey)})
        elif url.path == "/api/cost-report":
            # Cost attribution (cost-reduction 1.2): spend rollups from the run
            # records' spend blocks. Pure aggregation — safe to poll.
            import cost_report
            q = urllib.parse.parse_qs(url.query)
            try:
                days = int(q.get("days", [""])[0]) if q.get("days", [""])[0] else None
            except ValueError:
                self._send(400, {"error": "days must be a number"})
                return
            self._send(200, cost_report.report(days))
        elif url.path == "/api/trace-matrix":
            # Requirement traceability (roadmap 3.1): key -> scenario -> spec ->
            # gate commit -> CI health, one row per scenario. ?format=csv downloads.
            import trace_matrix
            q = urllib.parse.parse_qs(url.query)
            tkey = q.get("key", [""])[0]
            rows = trace_matrix.build(tkey or None)
            if q.get("format", [""])[0] == "csv":
                self._send(200, trace_matrix.to_csv(rows).encode("utf-8"),
                           ctype="text/csv")
            else:
                self._send(200, {"rows": rows, "fields": trace_matrix.FIELDS})
        elif url.path == "/api/pr-coverage":
            # Coverage-delta report for a key's latest run, rebuilt from the
            # persisted run record (the same report Workflow A posts on the PR).
            import pr_comment
            q = urllib.parse.parse_qs(url.query)
            key = q.get("key", [""])[0]
            recs = []
            for f in glob.glob(str(ROOT / "reports/runs/*.json")):
                if pathlib.Path(f).name in ("reviews.json", "queue.json",
                                            "hooks-seen.json"):
                    continue
                # Defensive parse: records are written non-atomically (tee), so
                # a request racing a live run — or one corrupt file — must skip
                # that record, not take the endpoint down for every key.
                try:
                    r = json.loads(pathlib.Path(f).read_text(encoding="utf-8"))
                except (json.JSONDecodeError, OSError):
                    continue
                if isinstance(r, dict) and r.get("trigger", {}).get("key") == key:
                    recs.append(r)
            recs.sort(key=lambda r: r.get("ts", 0))
            if not recs:
                self._send(404, {"error": f"no run recorded for {key}"})
                return
            md = pr_comment.from_record(recs[-1]) or \
                "_This run generated no tests and changed no coverage._"
            if q.get("download", [""])[0]:
                data = md.encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "text/markdown; charset=utf-8")
                self.send_header("Content-Disposition",
                                 f'attachment; filename="{key}-coverage.md"')
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)
                return
            self._send(200, {"key": key, "run_id": recs[-1].get("run_id", ""),
                             "markdown": md})
        elif url.path == "/api/explain":
            # "Why did the AI do that?" — assembled from evidence the run
            # already recorded. Nothing is inferred; a decision whose reason was
            # not written down comes back under `unexplained` with what is
            # missing, never as a plausible narrative.
            import explain as explain_lib
            q = urllib.parse.parse_qs(url.query)
            key = q.get("key", [""])[0]
            run = q.get("run", [""])[0]
            if key and not re.fullmatch(r"[\w.-]+", key):
                self._send(400, {"error": "key must be word characters, . or -"})
                return
            if run and not re.fullmatch(r"[\w.-]+", run):
                self._send(400, {"error": "run must be word characters, . or -"})
                return
            if not key and not run:
                self._send(400, {"error": "key or run required"})
                return
            self._send(200, explain_lib.explain(key=key or None, run_id=run or None))
        elif url.path == "/api/run-progress":
            # Per-RUN progress: which pipeline step a submitted request is on,
            # and — when it failed — which step, what the exit code means, and
            # the tail of that step's own log. /api/wizard/status answers the
            # journey question and collapses the run into one step; this is the
            # inside of that step.
            import run_progress
            q = urllib.parse.parse_qs(url.query)
            key = q.get("key", [""])[0]
            run = q.get("run", [""])[0]
            if key and not re.fullmatch(r"[\w.-]+", key):
                self._send(400, {"error": "key must be word characters, . or -"})
                return
            if run and not re.fullmatch(r"[\w.-]+", run):
                self._send(400, {"error": "run must be word characters, . or -"})
                return
            if not key and not run:
                self._send(400, {"error": "key or run required"})
                return
            self._send(200, run_progress.progress(key=key or None, run_id=run or None))
        elif url.path == "/api/wizard/status":
            # ONE aggregated progress answer for the guided flows (async by
            # nature: queued runs take minutes). Read-only — the wizard's
            # buttons drive the existing endpoints.
            import wizard_status
            q = urllib.parse.parse_qs(url.query)
            key = q.get("key", [""])[0]
            mode = q.get("mode", ["pr"])[0]
            if not re.fullmatch(r"[\w.-]+", key or "") or mode not in ("pr", "jira"):
                self._send(400, {"error": "key (word chars) and mode=pr|jira required"})
                return
            self._send(200, wizard_status.build(key, mode))
        elif url.path == "/api/trace":
            import trace as trace_lib          # ours; engine/lib precedes stdlib
            key = urllib.parse.parse_qs(url.query).get("key", [""])[0]
            if key:
                t = trace_lib.build(key)
                if not t["events"]:        # nothing anywhere for this key -> say so
                    self._send(404, {"error": f"no trace recorded for '{key}'"})
                    return
                self._send(200, t)
            else:
                self._send(200, {"keys": trace_lib.keys()[:50]})
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
                self._send(404, {"error": _err(e)})
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
            # Strict-charset basename, not just PurePosixPath().name: on Windows a
            # request like /runs/..\..\.env survives the posix .name (backslash is
            # not a posix separator) but IS a traversal for the pathlib join below.
            name = pathlib.PurePosixPath(url.path).name
            if not re.fullmatch(r"[\w.-]+", name) or ".." in name:
                return self._send(404, {"error": "not found"})
            f = ROOT / "reports/runs" / name
            self._send(200, f.read_bytes(), "text/plain; charset=utf-8") \
                if f.exists() else self._send(404, {"error": "not found"})
        elif url.path.endswith(".log"):
            name = pathlib.PurePosixPath(url.path).name
            if not re.fullmatch(r"[\w.-]+", name) or ".." in name:
                return self._send(404, {"error": "not found"})
            f = ROOT / "reports" / name
            self._send(200, f.read_bytes(), "text/plain; charset=utf-8") \
                if f.exists() else self._send(404, {"error": "not found"})
        else:
            self._send(404, {"error": "not found"})

    def do_POST(self):
        """Record the transaction, then run the real handler (observability 1.1).

        A WRAPPER, not 34 edits. The handler is one if/elif chain over 34
        mutating endpoints; touching each branch would guarantee the next
        endpoint someone adds is the one that goes unrecorded. Status comes from
        `_send`, which every branch already calls.

        Three deliberate choices:

        * GETs are NOT logged. Browsing is not a transaction, and audit noise
          makes the real entries harder to find.
        * The BODY is never stored. On the Settings path it carries `.env`
          values; the event records endpoint, actor, outcome and duration.
        * Emission happens in `finally` and the exception is re-raised, so a
          handler crash is recorded as `request.failed` and the server behaves
          exactly as it did before.
        """
        import time
        started = time.time()
        self._last_status = None
        try:
            self._handle_post()
        finally:
            code = self._last_status
            kind, outcome = _classify_status(code)
            actor = self.headers.get(SSO_HEADER) if SSO_HEADER else None
            event_log.emit(kind, actor=actor, source="ui",
                           target=urllib.parse.urlparse(self.path).path,
                           outcome=outcome,
                           duration_ms=(time.time() - started) * 1000,
                           detail={"status": code})

    def _handle_post(self):
        # /hooks/* is machine-to-machine ingest (OpenHands Agent Server): the sender
        # has no SSO header or UI token, so gate it on AIQE_HOOK_TOKEN (same contract
        # as the receiver on :4998 — X-AIQE-Token or Bearer) instead of UI auth.
        # With UI auth configured but no hook token, hooks stay closed (fail closed).
        if self.path.startswith("/hooks/"):
            hook_tok = os.environ.get("AIQE_HOOK_TOKEN", "")
            sent = self.headers.get("X-AIQE-Token", "") or \
                (self.headers.get("Authorization", "").removeprefix("Bearer ").strip())
            ui_locked = bool(UI_TOKEN or SSO_HEADER)
            if (hook_tok and sent != hook_tok) or (not hook_tok and ui_locked):
                return self._send(401, {"error": "hook auth: set AIQE_HOOK_TOKEN and "
                                                 "send it as X-AIQE-Token or Bearer"})
        elif not self._authed():
            return self._deny()
        body = self.rfile.read(int(self.headers.get("Content-Length", 0)) or 0)
        if self.path == "/api/waivers/save":
            try:
                p = json.loads(body or b"{}")
            except ValueError:
                return self._send(400, {"error": "invalid JSON"})
            key = str(p.get("key") or "").strip()
            if not key:
                return self._send(400, {"error": "key required"})
            actor = (self.headers.get(SSO_HEADER) if SSO_HEADER else None) or p.get("by") or ""
            rec, problems = waiver_store.save(key, p.get("scenario"), p.get("reason"),
                                              actor, p.get("expires"))
            if problems:
                # 422, and the problems are the POINT of the response: a refused
                # waiver must say what would make it acceptable.
                return self._send(422, {"error": "waiver refused", "problems": problems})
            # Saved, but say so if it protects nothing: the scenario id is
            # not in this ticket's signed spec, so the gate will keep refusing
            # whatever the author meant to waive. A warning, not a refusal —
            # waiving before the plan is authored is legitimate.
            warn = ("this scenario id is not in the signed spec for "
                    f"{key} — the waiver will not match anything. Check the id "
                    "in the plan (or approve the plan first)."
                    if waiver_store.unmatched(key, rec["scenario"]) else "")
            event_log.emit("settings.changed", actor=actor or None, source="ui",
                           target=f"waiver:{key}:{rec['scenario']}", outcome="ok",
                           detail={"expires": rec["expires"]})
            return self._send(200, {"ok": True, "waiver": rec,
                                    "warning": warn} if warn
                              else {"ok": True, "waiver": rec})
        if self.path == "/api/waivers/remove":
            try:
                p = json.loads(body or b"{}")
            except ValueError:
                return self._send(400, {"error": "invalid JSON"})
            gone = waiver_store.remove(str(p.get("key") or ""), str(p.get("scenario") or ""))
            return self._send(200 if gone else 404, {"ok": gone})
        if self.path == "/api/requirements/status":
            # Approve (signs the yaml's sha) or send back to draft. The same
            # call `make requirements-approve` makes — the UI is a second door
            # onto one mechanism, not a second mechanism.
            try:
                p = json.loads(body or b"{}")
            except ValueError:
                return self._send(400, {"error": "invalid JSON"})
            key = str(p.get("key") or "").strip()
            status = str(p.get("status") or "").strip()
            if not key or status not in ("draft", "approved"):
                return self._send(400, {"error": "key and status(draft|approved) required"})
            # Refuse to approve over an unanswered BLOCKING ambiguity. The whole
            # point of the state is that the ticket does not yet say what should
            # happen; approving anyway launders a guess into a signed artifact.
            if status == "approved":
                import spec_store
                blocking = [a for a in (spec_store.ambiguities(key) or [])
                            if isinstance(a, dict) and a.get("blocking")]
                if blocking:
                    return self._send(409, {
                        "error": "cannot approve: unanswered blocking ambiguity",
                        "blocking": blocking,
                        "hint": "answer it on the ticket and re-run "
                                f"`make requirements KEY={key}`"})
            actor = (self.headers.get(SSO_HEADER) if SSO_HEADER else None) or \
                p.get("by") or ""
            try:
                plan_state.set_requirements_status(key, status, by=actor)
            except SystemExit as e:
                return self._send(400, {"error": str(e)})
            event_log.emit("spec.requirements_approved" if status == "approved"
                           else "plan.revoked", actor=actor or None, source="ui",
                           target=key, outcome="ok", detail={"status": status})
            return self._send(200, {"ok": True, "key": key, "status": status})
        if self.path == "/api/alerts/save":
            # Story 3.1/3.2: define and enable rules without editing a file.
            # normalize() decides the shape and returns PROBLEMS rather than
            # raising, so a rule that will never match is saved and shown as
            # broken instead of silently rejected — the user needs to see why.
            try:
                p = json.loads(body or b"{}")
            except ValueError:
                return self._send(400, {"error": "invalid JSON"})
            incoming = p.get("rules")
            if not isinstance(incoming, list):
                return self._send(400, {"error": "rules must be a list"})
            if len(incoming) > 200:
                return self._send(400, {"error": "too many rules (max 200)"})
            cleaned, problems = [], {}
            for i, raw in enumerate(incoming):
                rule, probs = alert_rules.normalize(raw)
                rule["id"] = str(rule.get("id") or f"rule-{i + 1}")
                cleaned.append(rule)
                if probs:
                    problems[rule["id"]] = probs
            alert_rules.save({"rules": cleaned})
            event_log.emit("settings.changed", source="ui",
                           target="alert-rules", outcome="ok",
                           detail={"rules": len(cleaned),
                                   "with_problems": len(problems)})
            return self._send(200, {"saved": len(cleaned), "problems": problems})
        if self.path == "/api/alerts/test":
            # Deliberately a REAL send (story 3.2): the failure this catches is
            # a misconfigured channel, and a simulated one proves nothing.
            try:
                p = json.loads(body or b"{}")
            except ValueError:
                return self._send(400, {"error": "invalid JSON"})
            return self._send(200, alert_rules.test_fire(p.get("id", "")))
        if self.path == "/api/queue":
            try:
                p = json.loads(body or b"{}")
                target, pr = p["target"], p.get("pr")
                # Accept a pasted PULL-REQUEST URL in the target field. It carries the
                # repo slug and PR number (and, on Stash, the project key) — which is
                # what the user actually has in hand. Asking for a registry name plus
                # a PR number instead is what makes a Stash run fail on a project the
                # user never knew they had to configure.
                parsed = pr_url.parse(target) if p.get("mode") == "pr" else None
                if parsed:
                    target, pr = parsed["slug"], parsed["pr"]
                    if not repo_admin.is_registered(target):
                        self._send(400, {
                            "error": f"repo '{target}' from that PR URL is not "
                                     f"registered ({pr_url.describe(p['target'])})",
                            "hint": f"add it in Repositories — name {target}, "
                                    f"scm {parsed['kind']}, url "
                                    f"{parsed['project']}/{target}"
                                    + (f", Stash project {parsed['project']}"
                                       if parsed["kind"] == "stash" else "")})
                        return
                item, fresh = work_queue.add(p["mode"], target, pr,
                                             p.get("release", ""), "dashboard",
                                             force=bool(p.get("force")))
                self._send(200, {"queued": fresh, "item": item,
                                 "resolved_from_url": bool(parsed)})
            except (KeyError, json.JSONDecodeError, SystemExit) as e:
                self._send(400, {"error": _err(e)})
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
                self._send(400, {"error": _err(e)})
        elif self.path == "/api/review":
            try:
                p = json.loads(body or b"{}")
                # User-initiated transition: the key must exist somewhere (run,
                # plan, or prior entry) — else a typo invents a phantom board row.
                review_state.require_known(p["key"])
                entry = review_state.set_status(p["key"], p["status"],
                                                p.get("by") or self.user or "dashboard", p.get("note", ""))
                self._send(200, {"ok": True, "key": p["key"], "status": entry["status"]})
            except (KeyError, json.JSONDecodeError) as e:
                self._send(400, {"error": _err(e)})
            except SystemExit as e:        # unknown key / invalid status / missing note
                self._send(409, {"error": str(e)})
            except SystemExit as e:                     # invalid status
                self._send(400, {"error": _err(e)})
        elif self.path in ("/api/export/confluence", "/api/export/attach"):
            try:
                p = json.loads(body or b"{}")
                if self.path.endswith("confluence"):
                    result = export_plan.publish_to_confluence(
                        p["key"], p.get("space"), p.get("title"))
                else:
                    result = export_plan.attach_to_jira(
                        p["key"], p.get("format", "pdf"),
                        by=self.user or "dashboard")
                self._send(200, {"ok": True, "result": result})
            except (KeyError, json.JSONDecodeError) as e:
                self._send(400, {"error": _err(e)})
            except SystemExit as e:                     # no plan / publish or attach failure
                self._send(409, {"error": _err(e)})
        elif self.path in ("/api/queue/requeue", "/api/queue/remove"):
            try:
                item_id = json.loads(body or b"{}")["id"]
                fn = work_queue.requeue if self.path.endswith("requeue") else work_queue.remove
                self._send(200, {"ok": True, "item": fn(item_id)})
            except (KeyError, json.JSONDecodeError) as e:
                self._send(400, {"error": _err(e)})
            except SystemExit as e:          # library rejections (wrong status, unknown id)
                msg = _err(e)
                # A rate-limited retry is 429, not 409: a caller that retries on
                # 409 (a conflict it might resolve) would hammer the very limit
                # that just refused it. The message already carries the wait.
                self._send(429 if "RETRY_RATE_LIMITED" in msg else 409, {"error": msg})
        elif self.path == "/api/runs/retry":
            # Retry a FAILED RUN, not just a queue item. A run started from the
            # CLI or a webhook has no queue entry, so before this the only way
            # to re-run it was to know the CLI invocation — the UI could show
            # you a quarantined run and offer nothing to do about it.
            try:
                p = json.loads(body or b"{}")
                key = (p.get("key") or "").strip()
                if not re.fullmatch(r"[\w.-]+", key):
                    self._send(400, {"error": "key must be word characters, . or -"})
                    return
                import retry_policy
                verdict = retry_policy.check(key)
                if not verdict["allowed"] and not p.get("force"):
                    self._send(429, {"error": verdict["reason"], "retry": verdict})
                    return
                import run_progress
                prog = run_progress.progress(key=key)
                if prog["source"] == "none":
                    self._send(404, {"error": f"no run recorded for '{key}' — there "
                                              f"is nothing to retry"})
                    return
                if prog.get("busy"):
                    self._send(409, {"error": f"{key} is running right now; wait for "
                                              f"it to finish before retrying"})
                    return
                mode = "jira" if str(key).upper().startswith(("PROJ", "TICKET"))                     or not key.startswith("PR-") else "pr"
                if mode == "pr":
                    parts = key.split("-")
                    repo, num = "-".join(parts[1:-1]), parts[-1]
                    item, fresh = work_queue.add("pr", repo, pr=num,
                                                 requested_by="retry")
                else:
                    item, fresh = work_queue.add("jira", key, requested_by="retry")
                retry_policy.record(key)
                self._send(200, {"ok": True, "item": item, "queued": fresh,
                                 "retry": retry_policy.check(key)})
            except (KeyError, json.JSONDecodeError) as e:
                self._send(400, {"error": _err(e)})
            except SystemExit as e:
                self._send(409, {"error": _err(e)})
        elif self.path.startswith("/api/repos/"):
            try:
                p = json.loads(body or b"{}")
                if self.path == "/api/repos/app":
                    result = repo_admin.upsert_app(
                        p["name"], kind=p.get("kind"), scm=p.get("scm"),
                        url=p.get("url"), domains=p.get("domains"),
                        testable_paths=p.get("testable_paths"),
                        contract=p.get("contract"), route_table=p.get("route_table"),
                        consumes_services=p.get("consumes_services"),
                        stash_project=p.get("stash_project"))
                elif self.path == "/api/repos/test":
                    result = repo_admin.upsert_test(
                        p["name"], layer=p.get("layer"), framework=p.get("framework"),
                        scm=p.get("scm"), url=p.get("url"), specs=p.get("specs"),
                        fixtures=p.get("fixtures"), scope=p.get("scope"),
                        stash_project=p.get("stash_project"))
                elif self.path == "/api/repos/scope":
                    # `apps` is REQUIRED, even when clearing (send ""). Defaulting a
                    # missing field to empty silently erased a hand-managed scope and
                    # answered ok — a typo'd payload must not destroy configuration.
                    result = repo_admin.set_scope(p["test_repo"], p["apps"])
                elif self.path == "/api/repos/remove":
                    fn = (repo_admin.remove_test if p.get("section") == "test"
                          else repo_admin.remove_app)
                    result = fn(p["name"], force=bool(p.get("force")))
                elif self.path == "/api/repos/notes":
                    result = repo_admin.set_notes(p["repo"], p.get("text", ""))
                elif self.path == "/api/repos/guidance":
                    # Generate AGENTS.md for a repo that ships none of its own, so it
                    # stops contributing nothing to the knowledge every phase reads.
                    # A repo-owned file always wins, so this never overwrites theirs.
                    repo = p.get("repo")
                    rows = ([repo_guidance_gen.ensure(repo, force=bool(p.get("force")))]
                            if repo else
                            repo_guidance_gen.ensure_all(force=bool(p.get("force"))))
                    if any(r["status"] == "written" for r in rows):
                        guidance_sync.regenerate_agents_md()
                    result = {"generated": rows}
                elif self.path == "/api/repos/curated":
                    # Save (or delete, on empty content) the durable curated
                    # guidance, then refresh the estate knowledge immediately.
                    import curated_guidance
                    result = curated_guidance.save(p["repo"], p.get("file", "AGENTS.md"),
                                                   p.get("content", ""))
                    guidance_sync.regenerate_agents_md()
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
                self._send(400, {"error": _err(e)})
            except SystemExit as e:                     # validation failures
                self._send(400, {"error": _err(e)})
        elif self.path == "/api/settings":
            try:
                p = json.loads(body or b"{}")
                self._send(200, {"ok": True, **settings_store.save(p["updates"])})
            except (KeyError, json.JSONDecodeError) as e:
                self._send(400, {"error": _err(e)})
            except SystemExit as e:                     # unknown key / bad value
                self._send(400, {"error": _err(e)})
        elif self.path.startswith("/api/plans/"):
            try:
                p = json.loads(body or b"{}")
                key = p["key"]
                if self.path.endswith("/save"):
                    result = plan_state.save_plan(key, p.get("text", ""),
                                                  p.get("by") or self.user or "dashboard")
                elif self.path.endswith("/status"):
                    result = plan_state.set_status(key, p["status"],
                                                   p.get("by") or self.user or "dashboard",
                                                   p.get("note", ""))
                elif self.path.endswith("/link"):
                    plan_state.require_approved(key)
                    # attach_to_jira records the reference itself — see its docstring.
                    ref = export_plan.attach_to_jira(
                        key, p.get("format", "pdf"),
                        by=p.get("by") or self.user or "dashboard")
                    result = {**plan_state.get(key), "ref": ref}
                elif self.path.endswith("/comment"):
                    # J6: one ticket comment linking the plan AND the generated
                    # E2E tests (files, gate commits, branch) — the durable
                    # pointer from the JIRA ticket to everything produced for it.
                    result = plan_state.post_ticket_comment(key)
                elif self.path.endswith("/generate"):
                    plan_state.require_approved(key)   # fail fast before queueing
                    item, fresh = work_queue.add("tests", key, release="",
                                                 requested_by="dashboard-plan")
                    result = {"queued": fresh, "item": item}
                else:
                    self._send(404, {"error": "not found"}); return
                self._send(200, {"ok": True, **result})
            except (KeyError, json.JSONDecodeError) as e:
                self._send(400, {"error": _err(e)})
            except SystemExit as e:            # not approved / no plan / bad status
                self._send(409, {"error": _err(e)})
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
                self._send(400, {"error": _err(e)})
            except SystemExit as e:                     # no recipients / no run record
                self._send(400, {"error": _err(e)})
            except Exception as e:                      # SMTP failure — report, don't crash
                self._send(502, {"error": f"email failed: {e}"})
        elif self.path == "/api/integrations/check":
            try:
                p = json.loads(body or b"{}")
                self._send(200, integration_check.run(p.get("which")))
            except json.JSONDecodeError as e:
                self._send(400, {"error": _err(e)})
        elif self.path == "/api/demo/clear":
            # Run as a SUBPROCESS (like the page render) so a long-lived server always
            # executes the CURRENT clear targets. The old in-process call
            # froze the clear-target list at server start — a server running since before a
            # fix kept clearing the old set while the page promised the new one.
            try:
                p = json.loads(body or b"{}")
            except json.JSONDecodeError as e:
                self._send(400, {"error": _err(e)})
                return
            cmd = [sys.executable, str(ROOT / "engine/lib/demo_data.py"), "--json"]
            # Honor dry-run intent: silently ignoring {"dry": true} would turn a
            # caller's preview request into a REAL destructive clear.
            # Destructive flags resolve toward NOT destroying; `dry` resolves
            # toward the preview. See _json_flag.
            if _json_flag(p.get("dry"), unusable=True):
                cmd.append("--dry")
            if _json_flag(p.get("force"), unusable=False):
                cmd.append("--force")
            if _json_flag(p.get("factory"), unusable=False):
                cmd.append("--factory")
            r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True,
                               encoding="utf-8", errors="replace",
                               stdin=subprocess.DEVNULL)
            try:
                out = json.loads(r.stdout.strip().splitlines()[-1])
            except (ValueError, IndexError):
                self._send(500, {"error": f"clear failed: {(r.stderr or r.stdout)[:300]}"})
                return
            if out.get("ok"):
                self._send(200, out)
            else:                                       # refusal: a run looks active
                self._send(409, out)
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
        elif self.path == "/api/openhands/agent":
            # Launch a NAMED agent preset (pr-review, test-plan, …) — the same
            # roster as `qa.py openhands-run`, with an optional JIRA description
            # handed to the conversation as framed DATA (test-plan generation
            # from a pasted description, per the UI).
            try:
                p = json.loads(body or b"{}")
                import openhands_agents
                # Type-check up front: a JSON number/object in any field would
                # otherwise raise Type/AttributeError inside build() and reset
                # the connection instead of answering 400.
                fields = {k: p.get(k, "") for k in ("agent", "target", "description")}
                fields["pr"] = str(p.get("pr") or "")
                if not all(isinstance(v, str) for v in fields.values()):
                    self._send(400, {"error": "agent/target/description must be strings"})
                    return
                settings_store.load_env_into()
                if not openhands_mode.enabled():
                    self._send(409, {"error": "OpenHands is disabled (AIQE_OPENHANDS=off).",
                                     "hint": "Use the plan-only queue mode instead — "
                                             "the same job runs standalone."})
                    return
                try:
                    # Deterministic context: protocol, the E2E estate, what already
                    # exists for this key, the ticket (fetched when the caller did not
                    # paste one) and any extra note — ordered stable-first so launches
                    # share a cacheable prefix. See engine/lib/agent_context.py.
                    import agent_context
                    desc, comments, itype = fields["description"], "", ""
                    if fields["target"] and not desc:
                        t = agent_context.fetch_ticket(fields["target"])
                        desc = t.get("description", "")
                        comments, itype = t.get("comments", ""), t.get("issue_type", "")
                    ctx = agent_context.build(
                        key=fields["target"], description=desc, comments=comments,
                        issue_type=itype, extra=str(p.get("extra") or ""))
                    message = openhands_agents.build(
                        fields["agent"], fields["target"],
                        fields["pr"], fields["description"], context=ctx)
                except SystemExit as e:
                    self._send(400, {"error": _err(e)})
                    return
                title = f"AI-QE agent: {fields['agent']} {fields['target']}".strip()
                # Record the REQUEST before contacting OpenHands, so a failure below
                # is still traceable — a 502 used to answer the user and leave nothing.
                req_id = openhands_events.record_request(
                    f"agent:{fields['agent']}", key=fields["target"], title=title,
                    repo=os.environ.get("AIQE_CONTROL_REPO", ""),
                    agent=fields["agent"], message_chars=len(message))
                try:
                    result = openhands_client.start(
                        message, repo=os.environ.get("AIQE_CONTROL_REPO", "") or None,
                        title=title)
                except RuntimeError as e:
                    openhands_events.resolve_request(req_id, error=str(e))
                    raise
                openhands_events.resolve_request(
                    req_id, conversation_id=result.get("conversation_id", ""),
                    url=result.get("url", ""),
                    status="pending" if result.get("pending") else "")
                # Record it NOW. Webhook ingestion only fires if OpenHands can reach
                # a receiver we own; without this the conversation is real but
                # invisible, and the user cannot get back to it.
                openhands_events.record_launch(
                    result.get("conversation_id", ""), url=result.get("url", ""),
                    key=fields["target"], repo=os.environ.get("AIQE_CONTROL_REPO", ""),
                    title=title, source=f"agent:{fields['agent']}",
                    payload_chars=len(message))
                self._send(200, {"ok": True, **result})
            except (KeyError, json.JSONDecodeError) as e:
                self._send(400, {"error": _err(e)})
            except RuntimeError as e:
                self._send(502, {"error": _err(e)})
        elif self.path == "/api/openhands/trigger":
            # Start an OpenHands conversation (Path 1) for a work item.
            # Body fields:
            #   mode     "pr" | "jira"           (required)
            #   target   repo name or JIRA key   (required)
            #   pr       PR number               (required when mode=pr)
            #   release  fix-version string      (optional)
            #   message  override initial message (optional)
            #   repo     control repo override   (optional, defaults to AIQE_CONTROL_REPO)
            #   branch   branch inside repo      (optional, default "main")
            try:
                p = json.loads(body or b"{}")
                mode = p.get("mode")
                target = p.get("target", "")
                if mode not in ("pr", "jira"):
                    self._send(400, {"error": "mode must be pr|jira"}); return
                if not target:
                    self._send(400, {"error": "target is required"}); return
                if mode == "pr" and not p.get("pr"):
                    self._send(400, {"error": "pr is required for mode=pr"}); return

                settings_store.load_env_into()
                # Respect the hybrid switch: with OpenHands off, delegating a run to it
                # would silently contradict a deliberate standalone posture. Say so and
                # point at the paths that do work.
                if not openhands_mode.enabled():
                    self._send(409, {"error": "OpenHands is disabled (AIQE_OPENHANDS=off).",
                                     "hint": "Queue the run instead (Intake & queue), or "
                                             "trigger it from CI / the TaskEvent receiver."})
                    return
                ctrl_repo = p.get("repo") or os.environ.get("AIQE_CONTROL_REPO", "")
                branch = p.get("branch", "main")

                if p.get("message"):
                    message = p["message"]
                elif mode == "pr":
                    message = (f"Run the AI-QE pipeline: "
                               f"bash engine/pipeline.sh pr {target} {p['pr']}")
                else:
                    message = (f"Run the AI-QE pipeline: "
                               f"bash engine/pipeline.sh jira {target}")

                title = (f"AI-QE: {mode} {target}"
                         + (f" #{p['pr']}" if mode == "pr" else ""))
                req_id = openhands_events.record_request(
                    f"trigger:{mode}", key=str(target), title=title, repo=ctrl_repo,
                    message_chars=len(message))
                try:
                    result = openhands_client.start(
                        message, repo=ctrl_repo or None, branch=branch, title=title)
                except RuntimeError as e:
                    openhands_events.resolve_request(req_id, error=str(e))
                    raise
                openhands_events.resolve_request(
                    req_id, conversation_id=result.get("conversation_id", ""),
                    url=result.get("url", ""),
                    status="pending" if result.get("pending") else "")
                # See the /agent path: the launch is the first authoritative record,
                # webhooks only enrich it.
                openhands_events.record_launch(
                    result.get("conversation_id", ""), url=result.get("url", ""),
                    key=str(target), repo=ctrl_repo, title=title,
                    source=f"trigger:{mode}", payload_chars=len(message))
                self._send(200, {"ok": True, **result})
            except (KeyError, json.JSONDecodeError) as e:
                self._send(400, {"error": _err(e)})
            except RuntimeError as e:
                self._send(502, {"error": _err(e)})
        elif self.path in ("/hooks/openhands/events",
                           "/hooks/openhands/conversations"):
            # OpenHands Agent Server event stream — observability only.
            # The dashboard doubles as a lightweight webhook receiver so teams that
            # don't run the separate hook server at 4998 still get live visibility.
            # Mirrors the logic in bin/taskevent_receiver.py; always returns 200 so
            # a failing handler never triggers the sender's retry loop.
            try:
                p = json.loads(body or b"{}")
                if self.path.endswith("/events"):
                    r = openhands_events.record_events(p)
                else:
                    r = openhands_events.record_conversation(p)
                self._send(200, {"ok": True, **r})
            except Exception as e:                      # noqa: BLE001
                self._send(200, {"ok": False,
                                 "error": f"not recorded: {str(e)[:120]}"})
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
    try:
        srv = _Server((host, port), Handler)
    except OSError as e:
        # Actionable, because "[WinError 10048] ... normally permitted" tells a
        # user nothing about which knob to turn.
        sys.exit(f"cannot bind {host}:{port} — {e}\n"
                 f"A dashboard is probably already running there. Stop it, or "
                 f"pick another port with AIQE_UI_PORT=5000 make serve")
    srv.serve_forever()
