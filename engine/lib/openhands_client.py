#!/usr/bin/env python3
"""OpenHands Agent Server client — starts and polls conversations.

Reads OPENHANDS_URL and OPENHANDS_API_KEY from the environment (loaded from
.env by settings_store.load_env_into() before the dashboard server calls here).

Endpoint shape varies by deployment:
  self-hosted Agent Server : POST /api/conversations
                             GET  /api/conversations/<id>
  OpenHands Cloud (V1)     : POST /api/v1/app-conversations
                             GET  /api/v1/app-conversations?ids=<id>

Set OPENHANDS_CONVERSATIONS_PATH to override the POST path when deploying against
the Cloud API; the client detects the Cloud shape and polls via the V1 endpoint.

Auth:
  Agent Server : Authorization: Bearer <OPENHANDS_API_KEY>
  Cloud V1     : Authorization: Bearer <OPENHANDS_API_KEY>  (same header)

Conversation body:
  Agent Server : {"initial_user_msg": "...", "repository": "owner/repo"}
  Cloud V1     : {"initial_message": {"content": [{"type": "text", "text": "..."}]},
                  "selected_repository": "owner/repo", "selected_branch": "main"}

This module normalises both shapes so callers only see:
  start(message, repo, branch) -> {"conversation_id": ..., "url": ..., ...}
  status(conversation_id)      -> {"conversation_id": ..., "status": ..., ...}
  health()                     -> {"reachable": bool, "http_code": int, "error": str}
"""
import json, os, pathlib, socket, ssl, sys, time, urllib.error, urllib.parse, urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

TIMEOUT = 15
# Resolving a Cloud start-task into its conversation id happens inside a UI request,
# so the wait is short and bounded. Unresolved is reported, never guessed.
POLL_ATTEMPTS = 3
POLL_INTERVAL_S = 1.0
# Health check path — most self-hosted Agent Servers expose /health or /server_info;
# Cloud responds at /api/v1/users/me (auth-gated, 401 still proves reachability).
_HEALTH_CANDIDATES = ("/health", "/ready", "/server_info", "/api/v1/users/me")


def _ssl_context():
    """Return an unverified SSL context when AIQE_SSL_VERIFY=0 (corporate CA networks).
    Returns None (default verified behaviour) otherwise."""
    if os.environ.get("AIQE_SSL_VERIFY", "1").strip() == "0":
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        return ctx
    return None


def _make_opener():
    """Build a urllib opener that combines proxy and SSL settings.

    Uses the standard HTTPS_PROXY / NO_PROXY env vars (mapped from AIQE_HTTPS_PROXY
    / AIQE_NO_PROXY by settings_store.load_env_into()). No-arg ProxyHandler() reads
    those vars automatically, so NO_PROXY bypasses are respected without extra code.
    Respects AIQE_SSL_VERIFY=0 for corporate CA / self-signed certs.
    """
    handlers = []
    if os.environ.get("HTTPS_PROXY", "").strip() or os.environ.get("AIQE_HTTPS_PROXY", "").strip():
        handlers.append(urllib.request.ProxyHandler())
    ctx = _ssl_context()
    if ctx:
        handlers.append(urllib.request.HTTPSHandler(context=ctx))
    return urllib.request.build_opener(*handlers)


def _env(k, default=""):
    return os.environ.get(k, default).strip()


def _configured():
    """Returns (url, api_key) or raises RuntimeError."""
    url = _env("OPENHANDS_URL")
    key = _env("OPENHANDS_API_KEY")
    if not url:
        raise RuntimeError("OPENHANDS_URL is not set — configure it in Settings")
    return url.rstrip("/"), key



def _absolute(base, url):
    """Resolve a conversation URL the SERVER gave us against the configured base.

    OpenHands may return `url` as a path (`/conversations/abc`) rather than an
    absolute URL. It used to be stored verbatim, and the dashboard renders it
    straight into `<a href=...>` — so the browser resolved it against the
    DASHBOARD's own origin and the user landed on http://localhost:4999/... with
    "there is no OpenHands here". The configured OPENHANDS_URL was correct all
    along; the link simply did not use it.

    urljoin leaves an absolute URL untouched, so a deployment whose server
    returns a full URL is unaffected.
    """
    url = (url or "").strip()
    if not url:
        return ""
    if not base:
        return url
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme and parsed.netloc:
        return url                      # already absolute — the server's word
    return urllib.parse.urljoin(base.rstrip("/") + "/", url.lstrip("/"))


def _headers(api_key):
    """Both documented auth schemes at once.

    Cloud V1 takes `Authorization: Bearer <key>`; the self-hosted Agent Server
    authenticates session API keys with `X-Session-API-Key` (docs.openhands.dev
    /sdk/arch/agent-server). Sending only Bearer meant a self-hosted server
    rejected us with no hint — the same interoperability class as the
    conversations-path 405. Each server ignores the header it does not use, so
    sending both is safe and removes a whole category of setup failure."""
    h = {"Content-Type": "application/json", "Accept": "application/json"}
    if api_key:
        h["Authorization"] = f"Bearer {api_key}"
        h["X-Session-API-Key"] = api_key
    return h


def _request(method, url, headers, body=None):
    """Returns (status_code, parsed_json_or_None, error_string_or_None)."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with _make_opener().open(req, timeout=TIMEOUT) as resp:
            raw = resp.read()
            try:
                return resp.status, json.loads(raw) if raw else {}, None
            except json.JSONDecodeError:
                return resp.status, {}, None
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            body_obj = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            body_obj = {"_raw": raw.decode(errors="replace")[:300]}
        return e.code, body_obj, f"HTTP {e.code}"
    except (urllib.error.URLError, socket.timeout, ssl.SSLError, OSError) as e:
        return None, None, str(getattr(e, "reason", e))[:200]


def _is_cloud_path(path):
    return "/v1/app-conversations" in path or "/api/v1/" in path


def health():
    """Check Agent Server reachability and API-key acceptance.
    Returns {"reachable": bool, "http_code": int|None, "error": str, "hint": str}."""
    try:
        base, api_key = _configured()
    except RuntimeError as e:
        return {"reachable": False, "http_code": None,
                "error": str(e), "hint": "set OPENHANDS_URL in Settings"}

    override = _env("OPENHANDS_HEALTH_PATH")
    candidates = [override] if override else list(_HEALTH_CANDIDATES)
    h = _headers(api_key)

    for path in candidates:
        code, _, err = _request("GET", base + path, h)
        if err is None or (code is not None and code < 500):
            # 401/403 still means the server is reachable
            reachable = code is not None and code < 500
            hint = ""
            if code in (401, 403):
                hint = "server reachable but API key rejected — check OPENHANDS_API_KEY"
            return {"reachable": reachable, "http_code": code,
                    "error": "" if reachable else (err or f"HTTP {code}"),
                    "hint": hint, "endpoint": base + path}
    return {"reachable": False, "http_code": None,
            "error": f"no response from {base} on any health path",
            "hint": "check OPENHANDS_URL, network connectivity and OPENHANDS_HEALTH_PATH"}


def start(message, repo=None, branch="main", title=None, extra=None):
    """Start an OpenHands conversation.

    Args:
        message: the initial user message (the pipeline command to run)
        repo:    owner/repo (the control repo the agent should clone)
        branch:  branch inside that repo (default "main")
        title:   optional conversation title (displayed in the Cloud UI)
        extra:   dict merged into the request body (for custom fields)

    Returns a normalised dict:
        {"conversation_id": str, "start_task_id": str|None,
         "url": str, "status": str, "raw": dict}
    """
    base, api_key = _configured()
    conv_path = _env("OPENHANDS_CONVERSATIONS_PATH") or "/api/conversations"
    is_cloud = _is_cloud_path(conv_path)

    if is_cloud:
        # OpenHands Cloud V1 shape
        body = {
            "initial_message": {
                "content": [{"type": "text", "text": message}]
            },
        }
        if repo:
            body["selected_repository"] = repo
            body["selected_branch"] = branch
        if title:
            body["title"] = title
    else:
        # Self-hosted Agent Server shape
        body = {"initial_user_msg": message}
        if repo:
            body["repository"] = repo
    if extra:
        body.update(extra)

    code, resp, err = _request("POST", base + conv_path, _headers(api_key), body)

    # 404/405 on the conversations POST means the PATH is wrong for this
    # deployment, not that the request was bad: self-hosted Agent Server and
    # Cloud V1 expose different endpoints, and a raw "HTTP 405: Method not
    # allowed" sent users hunting for a bug in their ticket. Try the other
    # known shape (with its matching body) before giving up, and if both fail
    # say exactly which knob fixes it.
    if code in (404, 405) and not _env("OPENHANDS_CONVERSATIONS_PATH"):
        alt_path = "/api/v1/app-conversations" if not is_cloud else "/api/conversations"
        if _is_cloud_path(alt_path):
            alt_body = {"initial_message": {"content": [{"type": "text", "text": message}]}}
            if repo:
                alt_body["selected_repository"] = repo
                alt_body["selected_branch"] = branch
            if title:
                alt_body["title"] = title
        else:
            alt_body = {"initial_user_msg": message}
            if repo:
                alt_body["repository"] = repo
        if extra:
            alt_body.update(extra)
        alt_code, alt_resp, alt_err = _request("POST", base + alt_path,
                                               _headers(api_key), alt_body)
        if alt_code and alt_code < 400:
            conv_path, is_cloud = alt_path, _is_cloud_path(alt_path)
            code, resp, err = alt_code, alt_resp, alt_err
        elif alt_code in (404, 405) or alt_code is None:
            raise RuntimeError(
                f"OpenHands rejected both conversation endpoints "
                f"({conv_path} -> HTTP {code}, {alt_path} -> HTTP {alt_code}). "
                f"Set OPENHANDS_CONVERSATIONS_PATH to the path your deployment "
                f"exposes (Settings -> OpenHands), e.g. /api/conversations for a "
                f"self-hosted Agent Server or /api/v1/app-conversations for Cloud.")

    if err and code is None:
        raise RuntimeError(f"could not reach {base + conv_path}: {err}")
    if code and code >= 400:
        detail = (resp or {}).get("detail") or (resp or {}).get("error") or str(resp)
        hint = (" — if this is a path problem, set OPENHANDS_CONVERSATIONS_PATH "
                "in Settings to the endpoint your deployment exposes"
                if code in (404, 405) else "")
        raise RuntimeError(f"OpenHands returned HTTP {code} for {conv_path}: "
                           f"{detail}{hint}")

    resp = resp or {}

    # Cloud POST returns a START-TASK, whose `id` is the task's — NOT the
    # conversation's. Treating it as a conversation id handed callers an id and a
    # /conversations/<id> URL that OpenHands rejects, while the real conversation
    # existed under a different id: exactly the "link is invalid but a conversation
    # was created" report. `id` is only a conversation id on the self-hosted server.
    if is_cloud:
        conv_id = (resp.get("conversation_id")
                   or resp.get("app_conversation_id") or "")
        start_task_id = resp.get("start_task_id") or resp.get("id") or None
        # Resolve the real conversation id rather than returning a task id the user
        # cannot use. Bounded: this runs inside a UI request, and a start-task that
        # is still pending is reported honestly instead of guessed at.
        if not conv_id and start_task_id:
            for _ in range(POLL_ATTEMPTS):
                try:
                    resolved = poll_start_task(start_task_id)
                except RuntimeError:
                    break
                if resolved.get("conversation_id"):
                    conv_id = resolved["conversation_id"]
                    resp = {**resp, "resolved_start_task": resolved.get("raw") or {}}
                    break
                time.sleep(POLL_INTERVAL_S)
    else:
        conv_id = resp.get("conversation_id") or resp.get("id") or ""
        start_task_id = None

    # Never synthesise a URL from an id we do not have. A link built from a
    # start-task id looks authoritative and 404s.
    url = _absolute(base, resp.get("url")) or (f"{base}/conversations/{conv_id}" if conv_id else "")

    return {
        "conversation_id": conv_id,
        "start_task_id": start_task_id,
        "url": url,
        # The caller must be able to tell "started, here it is" from "accepted, still
        # spinning up" — otherwise the UI reports an id and a link it does not have.
        "pending": bool(start_task_id and not conv_id),
        "status": resp.get("status") or resp.get("execution_status") or "started",
        "raw": resp,
    }


def poll_start_task(start_task_id):
    """Poll a Cloud start-task until it yields an app_conversation_id.
    Returns the normalised start() dict or raises RuntimeError on failure."""
    base, api_key = _configured()
    url = f"{base}/api/v1/app-conversations/start-tasks?ids={start_task_id}"
    code, resp, err = _request("GET", url, _headers(api_key))
    if err and code is None:
        raise RuntimeError(f"could not reach start-task endpoint: {err}")
    items = (resp or {}).get("items") or []
    task = next((t for t in items if t.get("id") == start_task_id), resp or {})
    conv_id = task.get("app_conversation_id") or ""
    return {
        "conversation_id": conv_id,
        "start_task_id": start_task_id,
        "status": task.get("status") or "",
        "raw": task,
    }


def status(conversation_id):
    """Fetch the current status of a conversation.

    Works with both self-hosted and Cloud:
      self-hosted : GET /api/conversations/<id>
      Cloud V1    : GET /api/v1/app-conversations?ids=<id>

    Returns {"conversation_id": str, "status": str, "execution_status": str,
             "sandbox_status": str, "url": str, "raw": dict}
    """
    base, api_key = _configured()
    conv_path = _env("OPENHANDS_CONVERSATIONS_PATH") or "/api/conversations"
    h = _headers(api_key)

    if _is_cloud_path(conv_path):
        url = f"{base}/api/v1/app-conversations?ids={conversation_id}"
    else:
        url = f"{base}/api/conversations/{conversation_id}"

    code, resp, err = _request("GET", url, h)
    if err and code is None:
        raise RuntimeError(f"could not reach {url}: {err}")

    resp = resp or {}
    # Cloud wraps results in {"items": [...]}; agent server returns the object directly
    if "items" in resp:
        resp = next((i for i in resp["items"]
                     if i.get("id") == conversation_id or
                        i.get("conversation_id") == conversation_id),
                    resp.get("items", [{}])[0] if resp.get("items") else resp)

    cid = (resp.get("conversation_id") or resp.get("id") or resp.get("app_conversation_id")
           or conversation_id)
    conv_url = _absolute(base, resp.get("url")) or f"{base}/conversations/{cid}"
    return {
        "conversation_id": cid,
        "status": resp.get("status") or resp.get("sandbox_status") or "",
        "execution_status": resp.get("execution_status") or "",
        "sandbox_status": resp.get("sandbox_status") or "",
        "url": conv_url,
        "raw": resp,
    }


def events(conversation_id, limit=200):
    """Every event recorded for a conversation, oldest first.

    The webhook receiver (bin/taskevent_receiver.py) gets these PUSHED when
    OpenHands can reach a receiver we own. This is the PULL path, for callers
    that need the answer synchronously and cannot wait on a webhook that may
    never arrive — the LLM Runner's openhands adapter (multi-LLM 2.4).

    Endpoint differs by deployment, like every other call here:
      self-hosted : GET /api/conversations/<id>/events
      Cloud V1    : GET /api/v1/app-conversations/<id>/events
    Returns a list (empty when the deployment exposes no such endpoint —
    absence is not an error, the caller decides what to do about it)."""
    base, api_key = _configured()
    conv_path = _env("OPENHANDS_CONVERSATIONS_PATH") or "/api/conversations"
    if _is_cloud_path(conv_path):
        url = f"{base}/api/v1/app-conversations/{conversation_id}/events"
    else:
        url = f"{base}/api/conversations/{conversation_id}/events"
    code, resp, err = _request("GET", f"{url}?limit={int(limit)}", _headers(api_key))
    if err and code is None:
        raise RuntimeError(f"could not reach {url}: {err}")
    if code is not None and code >= 400:
        # "this deployment exposes no event stream" is not "the agent said
        # nothing" — a caller that cannot tell them apart reports a working
        # conversation as empty. Raise so the difference survives.
        raise LookupError(
            f"no readable event stream at {url} (HTTP {code}) — this OpenHands "
            f"deployment may not expose one; set OPENHANDS_CONVERSATIONS_PATH "
            f"for Cloud, or read the conversation in the UI")
    if isinstance(resp, list):
        return resp
    resp = resp or {}
    for key in ("events", "items", "results", "data"):
        if isinstance(resp.get(key), list):
            return resp[key]
    return []


def final_message(conversation_id):
    """The agent's LAST message in a conversation, or "" when there is none.

    Deliberately tolerant about event shape: OpenHands has more than one
    (self-hosted vs Cloud, and it moves between versions), and a provider that
    returns "" is handled — one that raises on an unfamiliar key is not."""
    texts = []
    for ev in events(conversation_id):
        if not isinstance(ev, dict):
            continue
        source = str(ev.get("source") or ev.get("role") or "")
        kind = str(ev.get("kind") or ev.get("type") or ev.get("action") or "")
        if source not in ("agent", "assistant") and "message" not in kind.lower():
            continue
        if source in ("user", "human"):
            continue
        content = (ev.get("message") or ev.get("content") or ev.get("text")
                   or (ev.get("args") or {}).get("content")
                   or (ev.get("extras") or {}).get("message"))
        if isinstance(content, list):        # [{"type":"text","text":...}]
            content = "".join(c.get("text", "") for c in content
                              if isinstance(c, dict))
        if isinstance(content, str) and content.strip():
            texts.append(content)
    return texts[-1] if texts else ""


if __name__ == "__main__":
    import sys as _sys
    _sys.stdout.reconfigure(encoding="utf-8")

    # Load .env defaults before running CLI checks
    try:
        import settings_store
        settings_store.load_env_into()
    except Exception:
        pass

    cmd = _sys.argv[1] if len(_sys.argv) > 1 else "health"
    if cmd == "health":
        print(json.dumps(health(), indent=2))
    elif cmd == "status" and len(_sys.argv) > 2:
        print(json.dumps(status(_sys.argv[2]), indent=2))
    elif cmd == "start" and len(_sys.argv) > 2:
        msg = _sys.argv[2]
        repo = _sys.argv[3] if len(_sys.argv) > 3 else None
        print(json.dumps(start(msg, repo), indent=2))
    else:
        print("usage: openhands_client.py health | status <id> | start <msg> [repo]")
