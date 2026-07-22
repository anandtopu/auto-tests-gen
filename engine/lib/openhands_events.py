#!/usr/bin/env python3
"""OpenHands webhook ingestion — live visibility into agent-driven runs.

When OpenHands orchestrates a run (trigger Path 1) the platform previously learned
nothing until the pipeline itself wrote its run record: a long conversation was
opaque, and a conversation that died never reported at all.

OpenHands' Agent Server can POST its event stream to a URL we own (`WebhookSpec`:
buffered, retried, custom auth headers). It appends two paths to the configured
base_url, which map onto our receiver:

    <base_url>/events         batches of agent events
    <base_url>/conversations  conversation lifecycle records

This module normalises both into a small, bounded per-conversation record so the
dashboard and CLI can show what an agent run is doing right now.

Deliberately defensive: the OpenHands event schema differs between versions, so
every field is read with fallbacks and an unrecognised payload is stored as a
counted "other" event rather than rejected — a webhook receiver that 500s just
triggers the sender's retry loop.

State: reports/openhands/state.json (outside reports/runs/, so no run-record glob
needs another exclusion). Override with AIQE_OPENHANDS_DIR.

CLI: openhands_events.py list | show <conversation_id> | prune
"""
import json, os, pathlib, sys, time

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import fs_lock

DIR = pathlib.Path(os.environ.get("AIQE_OPENHANDS_DIR") or ROOT / "reports/openhands")
FILE = DIR / "state.json"

MAX_CONVERSATIONS = 100      # keep the store bounded — this is a live view, not an archive
MAX_EVENTS_PER_CONV = 40     # most recent N per conversation
TERMINAL = ("finished", "error", "stopped", "cancelled", "complete", "completed")


def load():
    if FILE.exists():
        try:
            return json.load(open(FILE, encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            pass
    return {}


def _save(state):
    # Keep only the most recently updated conversations.
    if len(state) > MAX_CONVERSATIONS:
        keep = sorted(state.items(), key=lambda kv: kv[1].get("updated", 0),
                      reverse=True)[:MAX_CONVERSATIONS]
        state = dict(keep)
    DIR.mkdir(parents=True, exist_ok=True)
    FILE.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n",
                    encoding="utf-8", newline="\n")
    return state


def _first(d, *names, default=""):
    """First present, non-empty value among `names` (schemas vary by version)."""
    for n in names:
        v = d.get(n)
        if v not in (None, "", [], {}):
            return v
    return default


def conversation_id(obj):
    cid = _first(obj, "conversation_id", "conversationId", "id", "session_id",
                 "sessionId")
    if not cid and isinstance(obj.get("conversation"), dict):
        cid = _first(obj["conversation"], "conversation_id", "id")
    return str(cid) if cid else ""


def _kind(ev):
    return str(_first(ev, "kind", "type", "event_type", "action", "observation",
                      default="event"))


def _status_of(obj):
    """Any status-ish field, including the nested goal/state updates V1 emits."""
    s = _first(obj, "status", "execution_status", "state", "agent_state",
               "sandbox_status")
    if not s and isinstance(obj.get("value"), dict):
        s = _first(obj["value"], "status", "state")
    return str(s).lower() if s else ""


def _entry(state, cid):
    return state.setdefault(cid, {"conversation_id": cid, "status": "",
                                  "events": [], "event_count": 0,
                                  "first_seen": time.time(), "updated": 0,
                                  "repo": "", "key": "", "error": ""})


def record_events(payload):
    """Ingest an event batch. Accepts a list, a single object, or {"events": [...]}.
    Returns {"accepted": n, "conversations": [...]}"""
    batch = payload
    if isinstance(payload, dict):
        batch = payload.get("events") if isinstance(payload.get("events"), list) \
            else [payload]
    if not isinstance(batch, list):
        batch = [batch]
    touched, accepted = set(), 0
    with fs_lock.lock(FILE):
        state = load()
        for ev in batch:
            if not isinstance(ev, dict):
                continue
            cid = conversation_id(ev) or "unknown"
            e = _entry(state, cid)
            kind = _kind(ev)
            st = _status_of(ev)
            if st:
                e["status"] = st
            err = _first(ev, "error", "error_message", "exception")
            if err:
                e["error"] = str(err)[:300]
            e["events"].append({"kind": kind, "status": st,
                                "ts": _first(ev, "timestamp", "ts",
                                             default=time.time())})
            e["events"] = e["events"][-MAX_EVENTS_PER_CONV:]
            e["event_count"] += 1
            e["updated"] = time.time()
            touched.add(cid)
            accepted += 1
        _save(state)
    return {"accepted": accepted, "conversations": sorted(touched)}


def record_conversation(payload):
    """Ingest a conversation lifecycle record (created / status change / finished)."""
    objs = payload if isinstance(payload, list) else [payload]
    touched = []
    with fs_lock.lock(FILE):
        state = load()
        for obj in objs:
            if not isinstance(obj, dict):
                continue
            cid = conversation_id(obj) or "unknown"
            e = _entry(state, cid)
            st = _status_of(obj)
            if st:
                e["status"] = st
            repo = _first(obj, "selected_repository", "repository", "repo")
            if repo:
                e["repo"] = str(repo)
            # our own correlation hint, when the trigger passed one through
            key = _first(obj, "aiqe_key", "key", "title")
            if key:
                e["key"] = str(key)[:80]
            err = _first(obj, "error", "error_message")
            if err:
                e["error"] = str(err)[:300]
            e["updated"] = time.time()
            touched.append(cid)
        _save(state)
    return {"accepted": len(touched), "conversations": touched}


def summary(limit=25):
    """Most-recently-updated conversations, newest first."""
    state = load()
    rows = sorted(state.values(), key=lambda e: e.get("updated", 0), reverse=True)
    out = []
    for e in rows[:limit]:
        out.append({"conversation_id": e["conversation_id"],
                    "status": e.get("status", "") or "running",
                    "terminal": (e.get("status", "") in TERMINAL),
                    "repo": e.get("repo", ""), "key": e.get("key", ""),
                    "event_count": e.get("event_count", 0),
                    "error": e.get("error", ""),
                    "updated": e.get("updated", 0),
                    "last_event": (e["events"][-1]["kind"] if e.get("events") else "")})
    return out


def get(cid):
    return load().get(cid, {})


def prune(keep_terminal_hours=24):
    """Drop finished conversations older than the window."""
    cutoff = time.time() - keep_terminal_hours * 3600
    with fs_lock.lock(FILE):
        state = load()
        before = len(state)
        state = {k: v for k, v in state.items()
                 if not (v.get("status") in TERMINAL and v.get("updated", 0) < cutoff)}
        _save(state)
    return {"removed": before - len(state), "remaining": len(state)}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    a = sys.argv[1:]
    if not a or a[0] == "list":
        rows = summary()
        if not rows:
            print("no OpenHands conversations recorded yet — point WebhookSpec.base_url "
                  "at <receiver>/hooks/openhands (see docs/integrations/openhands.md)")
        else:
            print(f"{'conversation':<38} {'status':<12} {'events':>6}  repo / key")
            for r in rows:
                print(f"{r['conversation_id'][:38]:<38} {r['status']:<12} "
                      f"{r['event_count']:>6}  {r['repo'] or r['key']}")
    elif a[0] == "show" and len(a) > 1:
        print(json.dumps(get(a[1]), indent=2))
    elif a[0] == "prune":
        print(json.dumps(prune(), indent=2))
    else:
        sys.exit(__doc__)
