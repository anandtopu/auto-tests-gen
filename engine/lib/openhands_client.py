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
import json, os, pathlib, socket, ssl, sys, urllib.error, urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

TIMEOUT = 15
# Health check path — most self-hosted Agent Servers expose /health or /server_info;
# Cloud responds at /api/v1/users/me (auth-gated, 401 still proves reachability).
_HEALTH_CANDIDATES = ("/health", "/server_info", "/api/v1/users/me")


def _env(k, default=""):
    return os.environ.get(k, default).strip()


def _configured():
    """Returns (url, api_key) or raises RuntimeError."""
    url = _env("OPENHANDS_URL")
    key = _env("OPENHANDS_API_KEY")
    if not url:
        raise RuntimeError("OPENHANDS_URL is not set — configure it in Settings")
    return url.rstrip("/"), key


def _headers(api_key):
    h = {"Content-Type": "application/json", "Accept": "application/json"}
    if api_key:
        h["Authorization"] = f"Bearer {api_key}"
    return h


def _request(method, url, headers, body=None):
    """Returns (status_code, parsed_json_or_None, error_string_or_None)."""
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
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

    if err and code is None:
        raise RuntimeError(f"could not reach {base + conv_path}: {err}")
    if code and code >= 400:
        detail = (resp or {}).get("detail") or (resp or {}).get("error") or str(resp)
        raise RuntimeError(f"OpenHands returned HTTP {code}: {detail}")

    resp = resp or {}

    # Cloud returns a start-task object; the conversation_id arrives after polling.
    conv_id = (resp.get("conversation_id") or resp.get("id") or
               resp.get("app_conversation_id") or "")
    start_task_id = resp.get("id") if not conv_id else None
    # If the response IS a start-task (no conversation_id yet), store its id too
    if not conv_id and resp.get("id"):
        start_task_id = resp["id"]

    url = resp.get("url") or (f"{base}/conversations/{conv_id}" if conv_id else "")

    return {
        "conversation_id": conv_id,
        "start_task_id": start_task_id,
        "url": url,
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
    conv_url = resp.get("url") or f"{base}/conversations/{cid}"
    return {
        "conversation_id": cid,
        "status": resp.get("status") or resp.get("sandbox_status") or "",
        "execution_status": resp.get("execution_status") or "",
        "sandbox_status": resp.get("sandbox_status") or "",
        "url": conv_url,
        "raw": resp,
    }


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
