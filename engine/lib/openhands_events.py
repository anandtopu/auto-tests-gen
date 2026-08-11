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
import json, os, pathlib, sys, time, uuid

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import fs_lock

DIR = pathlib.Path(os.environ.get("AIQE_OPENHANDS_DIR") or ROOT / "reports/openhands")
FILE = DIR / "state.json"

MAX_CONVERSATIONS = 100      # keep the store bounded — this is a live view, not an archive
MAX_EVENTS_PER_CONV = 40     # most recent N per conversation
TERMINAL = ("finished", "error", "stopped", "cancelled", "complete", "completed")


def load():
    """Conversations, with every entry guaranteed to BE an entry.

    Guarded: corrupt -> quarantined, not silently empty (see fs_lock). That
    covers invalid JSON, not a valid file of the wrong SHAPE.

    THIS STORE WAS WRONGLY CLEARED BY THE SWEEP THAT FIXED THE OTHERS: the
    probe wrote to `conversations.json` while the real file is `state.json`, so
    it planted nothing, read an empty store and reported OK. Re-probed against
    the real path, `qa.py openhands` crashes at line 274. A probe that passes
    proves nothing until you know it exercised the thing.
    """
    return load_with_issues()[0]


def load_with_issues():
    """(conversations, malformed_ids) -- the same read, naming what it dropped."""
    raw = fs_lock.read_json_guarded(FILE, {})
    if not isinstance(raw, dict):
        return {}, ["<the conversations file is not an object>"]
    good = {k: v for k, v in raw.items() if isinstance(v, dict)}
    return good, sorted(set(raw) - set(good))


def _save(state):
    # Carry forward entries load() hid: six mutators here are load -> change ->
    # _save, so writing the filtered view would delete a malformed entry on the
    # next launch. A conversation record is what makes work someone is PAYING
    # for reachable again, so an unreadable one is still the only trace of it.
    raw = fs_lock.read_json_guarded(FILE, {})
    unreadable = ({k: v for k, v in raw.items()
                   if k not in state and not isinstance(v, dict)}
                  if isinstance(raw, dict) else {})
    # Keep only the most recently updated conversations. Trim BEFORE merging:
    # this sort reads kv[1]["updated"], so a malformed entry in the merged map
    # would crash the very write that is meant to preserve it.
    if len(state) > MAX_CONVERSATIONS:
        keep = sorted(state.items(), key=lambda kv: kv[1].get("updated", 0),
                      reverse=True)[:MAX_CONVERSATIONS]
        state = dict(keep)
    fs_lock.write_json_atomic(FILE, {**unreadable, **state}, sort_keys=True)
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
                                  "repo": "", "key": "", "error": "",
                                  "url": "", "title": "", "source": "",
                                  "request_id": "", "agent": ""})


def record_request(source, key="", title="", repo="", agent="", message_chars=0):
    """Record that we ASKED OpenHands to do something, before we know the outcome.

    `record_launch` can only record a request that succeeded and returned an id. A
    request that fails — OpenHands unreachable, credentials rejected, both
    conversation endpoints refused — answered the user with an error and left no trace
    at all, so "every request is traceable" was false for exactly the cases someone
    needs to investigate. A request that is merely ACCEPTED (Cloud start-task, no
    conversation id yet) was equally invisible.

    So the attempt is recorded first, keyed by a local request id. `resolve_request`
    later re-keys it to the conversation id (so webhook events merge onto the same
    entry) or marks it failed. Either way the row exists from the moment the user
    clicked.
    """
    # uuid, not timestamp+pid: two requests in the same second from the same process
    # would share an id, and the second would silently overwrite the first's record —
    # losing exactly the failed attempt someone is trying to trace.
    req_id = f"req-{int(time.time())}-{uuid.uuid4().hex[:8]}"
    with fs_lock.lock(FILE):
        state = load()
        e = _entry(state, req_id)
        e.update({"status": "requested", "request_id": req_id, "source": source,
                  "key": str(key or "")[:80], "repo": str(repo or "")[:200],
                  "title": str(title or "")[:300], "agent": str(agent or "")[:60],
                  "message_chars": int(message_chars or 0),
                  "requested_at": time.time(), "updated": time.time()})
        _save(state)
    return req_id


def resolve_request(req_id, conversation_id="", url="", status="", error=""):
    """Close out a recorded request.

    With a conversation id the entry is RE-KEYED to it, so the webhook stream (which
    only knows conversation ids) enriches the same row instead of creating a second
    one. Without an id the entry stays under its request id carrying the error — a
    failed request must remain visible, not vanish.
    """
    if not req_id:
        return {}
    cid = str(conversation_id or "").strip()
    with fs_lock.lock(FILE):
        state = load()
        e = state.pop(req_id, None)
        if e is None:
            e = {"conversation_id": cid or req_id, "events": [], "event_count": 0,
                 "first_seen": time.time(), "repo": "", "key": "", "error": ""}
        if cid:
            e["conversation_id"] = cid
            if url and not e.get("url"):
                e["url"] = str(url)[:300]
            if not e.get("status") or e["status"] == "requested":
                e["status"] = status or "launched"
            state[cid] = e
        else:
            e["conversation_id"] = req_id
            e["status"] = status or "failed"
            if error:
                e["error"] = str(error)[:300]
            state[req_id] = e
        e["updated"] = time.time()
        _save(state)
    return e


def record_launch(conversation_id, url="", key="", repo="", title="", source="",
                  payload_chars=0):
    """Record a conversation WE started, at the moment we start it.

    Webhook ingestion alone is not enough to track a conversation. The webhook only
    arrives if OpenHands can reach a receiver we own, which requires `WebhookSpec`
    base_url to be configured and routable — frequently it is not, and in a
    standalone/hybrid posture it may never be. Without this, launching an agent from
    the UI created a real conversation in OpenHands, showed its id in a toast, and
    then lost it forever: nothing in the platform could show it, so the user had no
    way back to work they had started (and paid for).

    So the launch itself is the first, authoritative record. Webhook events later
    enrich the SAME entry — `record_events`/`record_conversation` call `_entry` with
    the same conversation id, so status, event counts and errors flow in on top
    without creating a duplicate. If the webhook never arrives, the row still stands
    with its URL, and `url` is what actually lets the user go look at it.
    """
    cid = str(conversation_id or "").strip()
    if not cid:
        return {}
    with fs_lock.lock(FILE):
        state = load()
        e = _entry(state, cid)
        # Never regress a status the webhooks already established: a launch record
        # arriving late (retry, re-launch of the same id) must not un-finish a run.
        if not e.get("status"):
            e["status"] = "launched"
        for field, value in (("url", url), ("key", key),
                             ("repo", repo), ("title", title), ("source", source)):
            if value and not e.get(field):
                e[field] = str(value)[:300]
        # Payload size (cost-reduction 1.5a): the OpenHands-side LLM bill is
        # separate, but the launch's message size makes it attributable. Same
        # field the request-tracing path stores, so both paths converge.
        if payload_chars and not e.get("message_chars"):
            e["message_chars"] = int(payload_chars)
        e["updated"] = time.time()
        _save(state)
    return e


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
    # items(), not values(): the map KEY is the conversation id by construction
    # (`_entry` does state.setdefault(cid, {"conversation_id": cid, ...})), so
    # it is the right fallback for an entry that somehow lacks the field rather
    # than an invented one. This read was a bare e["conversation_id"], which
    # took out `qa.py openhands` entirely -- the surface whose whole job is
    # getting a user back to a conversation they are paying for.
    rows = sorted(state.items(), key=lambda kv: kv[1].get("updated", 0),
                  reverse=True)
    out = []
    for cid, e in rows[:limit]:
        out.append({"conversation_id": e.get("conversation_id") or cid,
                    "status": e.get("status", "") or "running",
                    "terminal": (e.get("status", "") in TERMINAL),
                    "repo": e.get("repo", ""), "key": e.get("key", ""),
                    "event_count": e.get("event_count", 0),
                    "error": e.get("error", ""),
                    "updated": e.get("updated", 0),
                    "payload_est_tokens": int(e.get("message_chars") or 0) // 4,
                    # `url` is the whole point of tracking a launch: without a way
                    # back to the conversation, knowing its id helps nobody.
                    "url": e.get("url", ""), "title": e.get("title", ""),
                    "source": e.get("source", ""),
                    "request_id": e.get("request_id", ""),
                    "agent": e.get("agent", ""),
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
            print("no OpenHands conversations recorded yet — conversations you START "
                  "are recorded here immediately; webhooks (WebhookSpec.base_url -> "
                  "<receiver>/hooks/openhands) add live progress on top")
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
