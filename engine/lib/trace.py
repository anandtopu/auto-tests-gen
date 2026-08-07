#!/usr/bin/env python3
"""Trace — the full chain for one key, as one chronological object.

The engineering-manager question this answers: *for this story/PR, show me
intent → plan (who approved, when) → generated tests → gate outcome → team review
→ release*, without stitching four views together by hand. Every link already
exists — plan_state history, review_state history (which also carries release and
the advisory critic), and the persisted run records — this module only JOINS them;
it never writes anything.

    build(key)  -> {"key", "trigger_type", "release", "review_status",
                    "plan_status", "events": [event, ...]}   # oldest first
    keys()      -> every key that has any trace at all

Event shape: {"ts", "kind", "title", "detail", "actor", "meta"} with kind one of
plan | run | review | release. Run events carry meta: run_id, mode, overall,
tests[], validate{}, critic{}, gates[] (repo/status/commit/diff).

CLI:  trace.py <KEY>   (also: bin/qa.py trace <KEY>)
"""
import glob, json, os, pathlib, sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

ROOT = pathlib.Path(__file__).resolve().parents[2]
STATE_FILES = ("reviews.json", "queue.json", "hooks-seen.json")


def _run_records():
    out = []
    for f in glob.glob(str(ROOT / "reports/runs/*.json")):
        if pathlib.Path(f).name in STATE_FILES:
            continue
        try:
            record = json.load(open(f, encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            continue
        if isinstance(record, dict) and isinstance(record.get("trigger"), dict):
            out.append(record)
    return out


def _plan_events(key):
    try:
        import plan_state
        entry = plan_state.get(key) or {}
    except (Exception, SystemExit):
        return []
    events = []
    for h in entry.get("history", []):
        status = h.get("status", "")
        note = h.get("note", "")
        # History rows are all shaped alike; the note distinguishes the milestone.
        if "linked" in note:
            title = "Plan linked to the ticket"
        elif "generated" in note:
            title = "Tests generated from the approved plan"
        elif "edited" in note:
            title = "Plan edited"
        else:
            title = {"draft": "Plan drafted", "in_review": "Plan in review",
                     "approved": "Plan approved",
                     "changes_requested": "Plan changes requested"}.get(
                status, f"Plan {status}")
        events.append({"ts": h.get("ts", 0), "kind": "plan", "title": title,
                       "detail": note, "actor": h.get("by", ""),
                       "meta": {"status": status}})
    return events


def _review_events(key):
    try:
        import review_state
        entry = review_state.load().get(key, {})
    except Exception:
        return []
    events = []
    for h in entry.get("history", []):
        if "release" in h:
            events.append({"ts": h.get("ts", 0), "kind": "release",
                           "title": f"Release set: {h['release']}",
                           "detail": f"source: {h.get('source', 'manual')}",
                           "actor": "", "meta": {"release": h["release"]}})
            continue
        status = h.get("status", "")
        title = {"pending_review": "Awaiting team review",
                 "in_review": "Team review started",
                 "approved": "Tests approved by the team",
                 "changes_requested": "Changes requested"}.get(
            status, f"Review {status}")
        events.append({"ts": h.get("ts", 0), "kind": "review", "title": title,
                       "detail": h.get("note", ""),
                       "actor": h.get("reviewer", ""), "meta": {"status": status}})
    return events


def _run_events(key, records):
    events = []
    for r in records:
        if r.get("trigger", {}).get("key") != key:
            continue
        contracts = {p.get("name"): p.get("contract") or {}
                     for p in r.get("phases", [])
                     if isinstance(p, dict) and p.get("name")}
        gen = contracts.get("generate", {})
        mode = r["trigger"].get("type", "?")
        title = {"pr": "Pipeline run (pull request)",
                 "jira": "Pipeline run (ticket)",
                 "tests": "Pipeline run (from approved plan)"}.get(
            mode, f"Pipeline run ({mode})")
        gates = [{"repo": g.get("test_repo"), "status": g.get("status"),
                  "commit": g.get("commit"), "diff": g.get("diff")}
                 for g in r.get("gates", []) if isinstance(g, dict)]
        committed = [g for g in gates if g["status"] == "committed"]
        detail = (f"{len(gen.get('tests', []))} test(s) generated; "
                  f"{len(committed)}/{len(gates)} repo(s) committed"
                  if gates else "no gates reached")
        events.append({"ts": r.get("ts", 0), "kind": "run", "title": title,
                       "detail": detail, "actor": "pipeline",
                       "meta": {"run_id": r.get("run_id"), "mode": mode,
                                "overall": r.get("overall"),
                                "tests": gen.get("tests", []),
                                "validate": contracts.get("validate", {}),
                                "critic": r.get("critic"),
                                "gates": gates}})
    return events


def build(key):
    """The full chain for `key`, events oldest-first. Total: an unknown key gives
    an empty chain, never an exception."""
    try:
        import plan_state
        plan_state.validate_key(key)
    except (Exception, SystemExit):
        return {"key": key, "trigger_type": "", "release": "",
                "review_status": "", "plan_status": "", "events": []}
    records = _run_records()
    events = _plan_events(key) + _review_events(key) + _run_events(key, records)
    events.sort(key=lambda e: e.get("ts", 0))

    trigger_type = next((e["meta"]["mode"] for e in events if e["kind"] == "run"), "")
    release = next((e["meta"]["release"] for e in reversed(events)
                    if e["kind"] == "release"), "")
    review_status = next((e["meta"]["status"] for e in reversed(events)
                          if e["kind"] == "review"), "")
    plan_status = next((e["meta"]["status"] for e in reversed(events)
                        if e["kind"] == "plan"), "")
    return {"key": key, "trigger_type": trigger_type, "release": release,
            "review_status": review_status, "plan_status": plan_status,
            "events": events}


def keys():
    """Every key with any trace, most recent activity first."""
    latest = {}
    for r in _run_records():
        k = r.get("trigger", {}).get("key")
        if k:
            latest[k] = max(latest.get(k, 0), r.get("ts", 0))
    try:
        import plan_state
        for e in plan_state.summary() or []:      # list of {key, status, updated, ...}
            k = e.get("key")
            if k:
                latest[k] = max(latest.get(k, 0), e.get("updated", 0) or 0)
    except Exception:
        pass
    try:
        import review_state
        for k, e in review_state.load().items():
            ts = max((h.get("ts", 0) for h in e.get("history", [])), default=0)
            latest[k] = max(latest.get(k, 0), ts)
    except Exception:
        pass
    return [k for k, _ in sorted(latest.items(), key=lambda kv: -kv[1])]


def render_text(t):
    """The chain as plain text — shared by this CLI and bin/qa.py trace."""
    import time as _t
    lines = [f"{t['key']}  trigger={t['trigger_type'] or '-'}  "
             f"plan={t['plan_status'] or '-'}  review={t['review_status'] or '-'}  "
             f"release={t['release'] or '-'}"]
    for e in t["events"]:
        when = _t.strftime("%Y-%m-%d %H:%M", _t.localtime(e["ts"])) if e["ts"] else "?"
        actor = f" [{e['actor']}]" if e.get("actor") else ""
        lines.append(f"  {when}  {e['kind']:<7} {e['title']}{actor}")
        if e.get("detail"):
            lines.append(f"                    {e['detail'][:100]}")
        if e["kind"] == "run":
            for g in e["meta"]["gates"]:
                sha = f" @{g['commit'][:7]}" if g.get("commit") else ""
                lines.append(f"                    gate {g['repo']}: {g['status']}{sha}")
            c = e["meta"].get("critic")
            if c:
                lines.append(f"                    critic {c.get('score')} "
                             f"{c.get('verdict')} (advisory)")
    return "\n".join(lines)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    if len(sys.argv) < 2:
        print("traceable keys (latest first):")
        for k in keys()[:20]:
            print(f"  {k}")
        sys.exit(0)
    t = build(sys.argv[1])
    if not t["events"]:
        sys.exit(f"no trace for {sys.argv[1]}")
    print(render_text(t))
