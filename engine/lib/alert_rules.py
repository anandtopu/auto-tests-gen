"""Alert rules over the transaction log (observability slice 3).

A rule is a question asked of the event stream on a schedule: "did three gate
refusals hit one repo within an hour?" Rules evaluate over the SAME log the
Activity view shows, so anything that fires is something a user can go and look
at — an alert whose evidence you cannot inspect trains people to ignore alerts.

Four properties carry the design, each answering a specific way alerting rots:

**Unevaluable is never healthy** (story 3.4). If the log could not be read, or
this process has been dropping events, the rule reports `unevaluable` with the
reason. Silence from a broken evaluator looks exactly like silence from a
healthy estate, and that is how monitoring lies. This is the same rule the cost
stack applies to unpriced spend: never present unmeasured as measured.

**Cooldown, so a flapping condition notifies once** (story 3.3). A condition
that crosses its threshold every minute must not produce fifty messages; the
first one already said everything. Firing is a STATE, not an event, so a rule
also resolves when the condition clears.

**Delivery goes through the Notify port.** This module never imports a vendor —
it shells out to `adapters/notify/*.sh` exactly as `coverage_drift` does, and
mock mode writes to the mock adapter rather than sending anything.

**Every attempt is recorded** (story 4.2 groundwork). `notify.sent` and
`notify.failed` land in the same log, so "nothing happened" is distinguishable
from "we failed to tell you" — which was finding F3.
"""
import datetime
import json
import os
import pathlib
import subprocess
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import app_paths                      # R12: mutable paths resolve here
import event_log
import fs_lock

ROOT = app_paths.ROOT

# Bounds that stop a rule being a denial-of-service on the evaluator. A rule
# asking for a 30-day window on every kind would scan the whole retained log on
# every tick; these are the ceilings, not defaults.
MAX_WINDOW_MINUTES = 7 * 24 * 60
MAX_SCAN = 5000
CHANNELS = ("slack", "email", "both")


def rules_file():
    v = (os.environ.get("AIQE_ALERT_RULES_FILE") or "").strip()
    if v:
        return pathlib.Path(v)
    return app_paths.state_root() / "reports" / "alert-rules.json"


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def load():
    return fs_lock.read_json_guarded(rules_file(), {"rules": []})


def save(doc):
    p = rules_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    with fs_lock.lock(p):
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(doc, indent=1), encoding="utf-8", newline="\n")
        os.replace(tmp, p)
    return doc


def normalize(rule):
    """Fill defaults and clamp. Returns (rule, problems).

    Problems are RETURNED rather than raised: a malformed rule must not stop
    the other rules being evaluated, and the UI needs to show what is wrong
    with it rather than 500.
    """
    problems = []
    r = dict(rule or {})
    r.setdefault("id", "")
    r["name"] = str(r.get("name") or r.get("id") or "unnamed")[:120]
    r["enabled"] = bool(r.get("enabled", True))

    match = dict(r.get("match") or {})
    kinds = [str(k).strip() for k in (match.get("kinds") or []) if str(k).strip()]
    unknown = [k for k in kinds if k not in event_log.KINDS]
    if unknown:
        # Not an error: a rule may legitimately predate a kind, or outlive one.
        # But it must be VISIBLE, or the rule silently never matches.
        problems.append(f"unknown kind(s) {sorted(unknown)} — this rule cannot match them")
    match["kinds"] = kinds
    match["outcome"] = (str(match.get("outcome") or "").strip() or None)
    match["target_contains"] = (str(match.get("target_contains") or "").strip() or None)
    if match["outcome"] and match["outcome"] not in event_log.OUTCOMES:
        problems.append(f"outcome {match['outcome']!r} is not one of {list(event_log.OUTCOMES)}")
        match["outcome"] = None
    if not kinds and not match["outcome"] and not match["target_contains"]:
        problems.append("matches EVERY event — narrow it with a kind, outcome or target")
    r["match"] = match

    try:
        r["threshold"] = max(1, int(r.get("threshold", 1)))
    except (TypeError, ValueError):
        r["threshold"] = 1
        problems.append("threshold was not a number; using 1")
    try:
        r["window_minutes"] = max(1, min(MAX_WINDOW_MINUTES,
                                         int(r.get("window_minutes", 60))))
    except (TypeError, ValueError):
        r["window_minutes"] = 60
        problems.append("window_minutes was not a number; using 60")
    try:
        r["cooldown_minutes"] = max(0, int(r.get("cooldown_minutes", 60)))
    except (TypeError, ValueError):
        r["cooldown_minutes"] = 60

    r["channel"] = r.get("channel") if r.get("channel") in CHANNELS else "slack"
    r["recipients"] = [str(x).strip() for x in (r.get("recipients") or []) if str(x).strip()]
    if r["channel"] in ("email", "both") and not r["recipients"]:
        problems.append("email channel with no recipients — nothing will be delivered")
    r["state"] = dict(r.get("state") or {})
    r["state"].setdefault("firing", False)
    r["state"].setdefault("last_fired", None)
    r["state"].setdefault("last_notified", None)
    r["state"].setdefault("last_resolved", None)
    return r, problems


def _matches(rule, ev):
    m = rule["match"]
    if m["kinds"] and ev.get("kind") not in m["kinds"]:
        return False
    if m["outcome"] and ev.get("outcome") != m["outcome"]:
        return False
    if m["target_contains"] and m["target_contains"] not in str(ev.get("target") or ""):
        return False
    return True


def evaluate(now=None, notify=True):
    """Evaluate every enabled rule. Returns a per-rule status list.

    Never raises: this runs from `make maintain`, and a broken rule must not
    stop the rest of maintenance.
    """
    now = now or _now()
    doc = load()
    out, changed = [], False

    health = event_log.health()
    since = _iso(now - datetime.timedelta(minutes=MAX_WINDOW_MINUTES))
    try:
        events, corrupt = event_log.read(limit=MAX_SCAN, since=since)
        read_error = None
    except Exception as e:                      # noqa: BLE001
        events, corrupt, read_error = [], 0, str(e)

    for raw in doc.get("rules") or []:
        rule, problems = normalize(raw)
        if not rule["enabled"]:
            out.append({"id": rule["id"], "name": rule["name"], "status": "disabled",
                        "problems": problems})
            continue

        # 3.4 — an evaluator that cannot see the data says so. Reporting "ok"
        # here would mean a broken log reads as a healthy estate.
        if read_error or health.get("degraded"):
            reason = read_error or (
                f"this process dropped {health.get('dropped')} event(s); "
                f"the window may be incomplete")
            out.append({"id": rule["id"], "name": rule["name"],
                        "status": "unevaluable", "reason": reason,
                        "problems": problems})
            continue

        cutoff = _iso(now - datetime.timedelta(minutes=rule["window_minutes"]))
        hits = [e for e in events
                if (e.get("ts") or "") >= cutoff and _matches(rule, e)]
        firing_now = len(hits) >= rule["threshold"]
        st = rule["state"]
        was = bool(st.get("firing"))
        transition = None

        if firing_now and not was:
            transition = "fired"
            st["firing"] = True
            st["last_fired"] = _iso(now)
        elif was and not firing_now:
            transition = "resolved"
            st["firing"] = False
            st["last_resolved"] = _iso(now)

        # Cooldown gates the MESSAGE, not the state. The rule keeps tracking
        # reality; we simply stop repeating ourselves.
        may_notify = True
        if transition == "fired" and st.get("last_notified"):
            elapsed = (now - datetime.datetime.strptime(
                st["last_notified"], "%Y-%m-%dT%H:%M:%SZ").replace(
                    tzinfo=datetime.timezone.utc)).total_seconds() / 60.0
            may_notify = elapsed >= rule["cooldown_minutes"]

        if transition:
            changed = True
            event_log.emit(f"alert.{transition}", source="cron",
                           target=rule["name"], outcome="ok",
                           detail={"hits": len(hits), "threshold": rule["threshold"],
                                   "window_minutes": rule["window_minutes"],
                                   "notified": bool(notify and may_notify)})
            if notify and may_notify:
                msg = (f"[AI-QE alert] {rule['name']} {transition.upper()}: "
                       f"{len(hits)} matching event(s) in {rule['window_minutes']}m "
                       f"(threshold {rule['threshold']})")
                deliver(msg, rule["channel"], rule["recipients"], rule["name"])
                st["last_notified"] = _iso(now)

        raw.update(rule)
        out.append({"id": rule["id"], "name": rule["name"],
                    "status": "firing" if st["firing"] else "ok",
                    "hits": len(hits), "threshold": rule["threshold"],
                    "transition": transition, "corrupt_lines": corrupt,
                    "problems": problems})

    if changed:
        save(doc)
    return out


def deliver(msg, channel="slack", recipients=(), rule_name=""):
    """Send through the Notify port and RECORD the attempt (F3).

    Best-effort delivery, but never silent: `notify.failed` is what makes
    "we could not tell you" different from "nothing happened".
    """
    import work_queue
    mock = os.environ.get("AIQE_MOCK", "1") == "1"
    targets = []
    if channel in ("slack", "both"):
        targets.append(ROOT / ("adapters/mock/notify.sh" if mock
                               else "adapters/notify/slack.sh"))
    if channel in ("email", "both"):
        targets.append(ROOT / "adapters/notify/email.sh")

    ok_any = False
    for adapter in targets:
        try:
            env = dict(os.environ)
            if recipients:
                env["EMAIL_TO"] = ",".join(recipients)
            r = subprocess.run([work_queue.bash_exe(), str(adapter), "post", msg],
                               cwd=ROOT, stdin=subprocess.DEVNULL, timeout=30,
                               capture_output=True, env=env)
            sent = r.returncode == 0
            ok_any = ok_any or sent
            event_log.emit("notify.sent" if sent else "notify.failed",
                           source="cron", target=rule_name or adapter.name,
                           outcome="ok" if sent else "failed",
                           detail={"adapter": adapter.name, "channel": channel,
                                   "rc": r.returncode})
        except Exception as e:                  # noqa: BLE001
            event_log.emit("notify.failed", source="cron",
                           target=rule_name or adapter.name, outcome="failed",
                           detail={"adapter": adapter.name, "error": str(e)[:200]})
    return ok_any


def test_fire(rule_id):
    """Send a rule's message through its REAL channel (story 3.2).

    Deliberately not a dry run: the failure this catches is a misconfigured
    channel, and a simulated send proves nothing about it. Recorded like any
    other delivery so the attempt is visible in the Activity view.
    """
    doc = load()
    for raw in doc.get("rules") or []:
        if str(raw.get("id")) == str(rule_id):
            rule, problems = normalize(raw)
            ok = deliver(f"[AI-QE alert] TEST of rule {rule['name']!r} — "
                         f"if you can read this, delivery works.",
                         rule["channel"], rule["recipients"], rule["name"])
            return {"ok": ok, "problems": problems, "channel": rule["channel"]}
    return {"ok": False, "problems": [f"no rule with id {rule_id!r}"]}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    argv = sys.argv[1:]
    if argv and argv[0] == "test-fire":
        print(json.dumps(test_fire(argv[1] if len(argv) > 1 else ""), indent=1))
    else:
        print(json.dumps(evaluate(notify="--no-notify" not in argv), indent=1))
