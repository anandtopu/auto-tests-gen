#!/usr/bin/env python3
"""Validate connectivity to every external system this platform integrates with.

Answers one question per integration: *with the credentials configured right now,
can we actually reach it?* — so a team can prove their setup before switching
AIQE_MOCK=0, instead of discovering it mid-run.

**Every check is read-only and non-destructive.** Nothing here posts a comment,
pushes a branch, sends an email, or starts an OpenHands conversation. Where a
capability genuinely cannot be verified without a side effect (a Slack webhook only
answers to a real POST), the check says so rather than pretending.

Each result is {name, status, detail, hint}:
  ok       reached it, credentials accepted
  fail     configured but unreachable / rejected — `hint` says what to do
  skipped  not configured (not an error; most estates use a subset)

Credential VALUES are never echoed — only whether they are set.

CLI:  integration_check.py [--json] [name ...]
      make check-integrations            (also POST /api/integrations/check)
For the deeper, OpenHands-specific staged test — including an opt-in live
conversation that costs money — use `make smoke-openhands`.
"""
import json, os, pathlib, socket, ssl, subprocess, sys, urllib.error, urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import work_queue

TIMEOUT = 8


def _env(k, d=""):
    return os.environ.get(k, d).strip()


def _load_env():
    try:
        import settings_store
        settings_store.load_env_into()
    except Exception:
        pass


def _r(name, status, detail, hint=""):
    return {"name": name, "status": status, "detail": detail, "hint": hint}


def _ssl_context():
    """Return an unverified SSL context when AIQE_SSL_VERIFY=0 (corporate CA networks)."""
    if _env("AIQE_SSL_VERIFY", "1") == "0":
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    return None


def _http(url, headers=None, method="GET"):
    """Returns (code, error). code None means the request never completed."""
    req = urllib.request.Request(url, headers=headers or {}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT, context=_ssl_context()) as resp:
            return resp.status, None
    except urllib.error.HTTPError as e:
        return e.code, None
    except (urllib.error.URLError, socket.timeout, ssl.SSLError, OSError) as e:
        return None, str(getattr(e, "reason", e))[:120]


def _http_check(name, url, headers=None, hint=""):
    code, err = _http(url, headers)
    if err is not None:
        return _r(name, "fail", f"cannot reach {url}: {err}",
                  hint or "check the URL, network egress and any proxy")
    if code in (401, 403):
        return _r(name, "fail", f"reachable but credentials rejected (HTTP {code})",
                  hint or "check the token and its scopes")
    if code and code < 400:
        return _r(name, "ok", f"reachable (HTTP {code})")
    return _r(name, "fail", f"unexpected response (HTTP {code})", hint)


def _adapter(path, *args):
    """Run an adapter verb; returns (returncode, stdout, stderr)."""
    r = subprocess.run([work_queue.bash_exe(), str(ROOT / path), *args],
                       cwd=ROOT, capture_output=True, text=True,
                       encoding="utf-8", errors="replace",
                       stdin=subprocess.DEVNULL, timeout=60)
    return r.returncode, r.stdout, r.stderr


# ------------------------------------------------------------------ checks

def check_llm():
    if not _env("ANTHROPIC_API_KEY"):
        return _r("LLM (Anthropic)", "skipped", "ANTHROPIC_API_KEY not set",
                  "required only for real phases (AIQE_MOCK=0)")
    have_cli = subprocess.run([work_queue.bash_exe(), "-lc", "command -v claude"],
                              capture_output=True, text=True,
                              encoding="utf-8", errors="replace",
                              stdin=subprocess.DEVNULL).returncode == 0
    # Deliberately no API call here — a probe would bill the account. Presence only.
    return _r("LLM (Anthropic)", "ok",
              "API key set" + ("; claude CLI on PATH" if have_cli
                               else "; claude CLI NOT on PATH"),
              "" if have_cli else "install the Claude Code CLI for real phases")


def check_scm():
    kind = _env("SCM_KIND", "github")
    name = f"SCM ({kind})"
    tok = {"github": "GITHUB_TOKEN", "bitbucket": "BITBUCKET_TOKEN",
           "stash": "STASH_TOKEN"}.get(kind, "")
    if not _env(tok):
        return _r(name, "skipped", f"{tok} not set")
    if kind == "stash" and not (_env("STASH_URL") and _env("STASH_PROJECT")):
        return _r(name, "fail", "STASH_URL / STASH_PROJECT not set",
                  "both are required for the Bitbucket Server adapter")
    try:
        from registry import load_registry
        repos = [r["name"] for r in load_registry()["source_repositories"]]
    except Exception:
        repos = []
    if not repos:
        return _r(name, "skipped", "no source repositories registered")
    repo = _env("AIQE_SMOKE_REPO") or repos[0]
    try:
        rc, out, err = _adapter(f"adapters/scm/{kind}.sh", "fetch_file", repo, "README.md")
    except subprocess.TimeoutExpired:
        return _r(name, "fail", f"timed out reading {repo}", "check network egress")
    # exit 3 == file absent, which still proves the API call and auth succeeded
    if rc in (0, 3):
        return _r(name, "ok", f"read-only API call against '{repo}' succeeded"
                              + (" (README.md absent, auth fine)" if rc == 3 else ""))
    return _r(name, "fail", f"read of '{repo}' failed: {(err or out).strip()[:120]}",
              "check the token scopes and that the repo slug/owner is correct")


def check_tracker():
    if not _env("ATLASSIAN_MCP_TOKEN"):
        return _r("JIRA", "skipped", "ATLASSIAN_MCP_TOKEN not set")
    key = _env("AIQE_SMOKE_TICKET")
    if not key:
        return _r("JIRA", "skipped", "token set, but no ticket to read",
                  "set AIQE_SMOKE_TICKET=PROJ-123 to verify the read path")
    try:
        rc, out, err = _adapter("adapters/tracker/jira.sh", "get_item", key)
    except subprocess.TimeoutExpired:
        return _r("JIRA", "fail", f"timed out reading {key}", "check JIRA_URL and network")
    if rc == 0 and out.strip().startswith("{"):
        return _r("JIRA", "ok", f"read ticket {key}")
    return _r("JIRA", "fail", f"could not read {key}: {(err or out).strip()[:120]}",
              "check JIRA_URL, the API token and its project permissions")


def check_confluence():
    url = _env("CONFLUENCE_URL")
    if not url:
        return _r("Confluence", "skipped", "CONFLUENCE_URL not set")
    tok = _env("ATLASSIAN_MCP_TOKEN")
    headers = {"Authorization": f"Bearer {tok}"} if tok else {}
    return _http_check("Confluence", url.rstrip("/") + "/rest/api/space", headers,
                       "check CONFLUENCE_URL and the Atlassian token")


def check_openhands():
    if not _env("OPENHANDS_URL"):
        return _r("OpenHands", "skipped", "OPENHANDS_URL not set")
    try:
        import openhands_client
        h = openhands_client.health()
    except Exception as e:
        return _r("OpenHands", "fail", f"client error: {str(e)[:120]}",
                  "check OPENHANDS_URL and OPENHANDS_API_KEY")
    if not h["reachable"]:
        return _r("OpenHands", "fail", h.get("error") or f"HTTP {h.get('http_code')}",
                  h.get("hint") or "check OPENHANDS_URL and network")
    detail = f"reachable at {h.get('endpoint', _env('OPENHANDS_URL'))} " \
             f"(HTTP {h.get('http_code')})"
    if h.get("hint"):
        return _r("OpenHands", "fail", detail, h["hint"])
    if not _env("OPENHANDS_API_KEY"):
        detail += " — OPENHANDS_API_KEY not set (required to start conversations)"
    else:
        detail += " — for the full staged test: make smoke-openhands"
    return _r("OpenHands", "ok", detail)


def check_cicd():
    url = _env("JENKINS_URL")
    if not url:
        return _r("Jenkins", "skipped", "JENKINS_URL not set")
    import base64
    headers = {}
    if _env("JENKINS_USER") and _env("JENKINS_API_TOKEN"):
        raw = f"{_env('JENKINS_USER')}:{_env('JENKINS_API_TOKEN')}".encode()
        headers["Authorization"] = "Basic " + base64.b64encode(raw).decode()
    return _http_check("Jenkins", url.rstrip("/") + "/api/json", headers,
                       "check JENKINS_URL, user and API token")


def check_slack():
    url = _env("SLACK_WEBHOOK_URL")
    if not url:
        return _r("Slack", "skipped", "SLACK_WEBHOOK_URL not set")
    if not url.startswith("https://hooks.slack.com/"):
        return _r("Slack", "fail", "URL is not a hooks.slack.com webhook",
                  "copy the Incoming Webhook URL from the Slack app config")
    # A webhook only answers a real POST, which would spam the channel — so we verify
    # the endpoint host is reachable and report the limit honestly.
    host = url.split("/")[2]
    try:
        socket.create_connection((host, 443), timeout=TIMEOUT).close()
    except OSError as e:
        return _r("Slack", "fail", f"cannot reach {host}: {e}", "check network egress")
    return _r("Slack", "ok", "webhook URL well-formed and host reachable "
                             "(not posted — that would notify the channel)")


def check_smtp():
    host = _env("SMTP_HOST")
    if not host:
        return _r("Email (SMTP)", "skipped",
                  "SMTP_HOST not set — emails are written to out/mock-email/")
    import smtplib
    port = int(_env("SMTP_PORT") or "587")
    sec = (_env("SMTP_SECURITY") or "starttls").lower()
    try:
        if sec == "ssl":
            srv = smtplib.SMTP_SSL(host, port, timeout=TIMEOUT,
                                   context=ssl.create_default_context())
        else:
            srv = smtplib.SMTP(host, port, timeout=TIMEOUT)
        try:
            srv.ehlo()
            if sec == "starttls":
                srv.starttls(context=ssl.create_default_context())
                srv.ehlo()
            if _env("SMTP_USER"):
                srv.login(_env("SMTP_USER"), _env("SMTP_PASSWORD"))
                detail = f"connected to {host}:{port} ({sec}) and authenticated"
            else:
                detail = f"connected to {host}:{port} ({sec}), no auth configured"
        finally:
            try: srv.quit()
            except Exception: pass
    except smtplib.SMTPAuthenticationError:
        return _r("Email (SMTP)", "fail", "server reached but login was rejected",
                  "check SMTP_USER / SMTP_PASSWORD (app password for Gmail/O365)")
    except Exception as e:
        return _r("Email (SMTP)", "fail", f"cannot connect to {host}:{port}: "
                                          f"{str(e)[:110]}",
                  "check host, port and SMTP_SECURITY (starttls|ssl|none)")
    # No message is sent — delivery is only exercised by `make email`.
    return _r("Email (SMTP)", "ok", detail + " — no mail sent")


def check_telemetry():
    url = _env("SPLUNK_HEC_URL")
    if not url:
        return _r("Splunk HEC", "skipped", "SPLUNK_HEC_URL not set")
    host = url.split("/")[2] if "//" in url else url
    hostname = host.split(":")[0]
    port = int(host.split(":")[1]) if ":" in host else 443
    try:
        socket.create_connection((hostname, port), timeout=TIMEOUT).close()
    except OSError as e:
        return _r("Splunk HEC", "fail", f"cannot reach {hostname}:{port}: {e}",
                  "check SPLUNK_HEC_URL and network egress")
    return _r("Splunk HEC", "ok", f"{hostname}:{port} reachable "
                                  "(no event sent)")


CHECKS = {
    "llm": check_llm, "scm": check_scm, "jira": check_tracker,
    "confluence": check_confluence, "openhands": check_openhands,
    "jenkins": check_cicd, "slack": check_slack, "smtp": check_smtp,
    "splunk": check_telemetry,
}


def run(names=None):
    """Run the requested checks (all by default). Never raises — a broken check
    is reported as a failure, not an exception."""
    _load_env()
    picked = [n for n in (names or CHECKS) if n in CHECKS] or list(CHECKS)
    results = []
    for n in picked:
        try:
            results.append({**CHECKS[n](), "id": n})
        except Exception as e:                      # a check must never take the page down
            results.append({**_r(n, "fail", f"check raised: {str(e)[:120]}",
                                 "this is a bug in the checker"), "id": n})
    summary = {s: sum(1 for r in results if r["status"] == s)
               for s in ("ok", "fail", "skipped")}
    return {"results": results, "summary": summary,
            "mock_mode": os.environ.get("AIQE_MOCK", "1") == "1"}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    out = run(args or None)
    if "--json" in sys.argv:
        print(json.dumps(out, indent=2))
        sys.exit(0)
    mark = {"ok": "[ OK ]", "fail": "[FAIL]", "skipped": "[skip]"}
    print("Integration checks (read-only; nothing is posted, pushed or sent)\n")
    for r in out["results"]:
        print(f"{mark[r['status']]} {r['name']:<18} {r['detail']}")
        if r["hint"] and r["status"] != "ok":
            print(f"        -> {r['hint']}")
    s = out["summary"]
    print(f"\n{s['ok']} ok · {s['fail']} failed · {s['skipped']} not configured")
    if out["mock_mode"]:
        print("Note: AIQE_MOCK=1 — runs still use mock adapters. These checks probe the "
              "REAL systems, so you can verify credentials before switching to real mode.")
    sys.exit(1 if s["fail"] else 0)
