#!/usr/bin/env python3
"""Team-review state for AI-generated artifacts, per PR / JIRA key.

Lifecycle: a pipeline run that COMMITS generated tests marks its key
`pending_review` (a fresh commit also resets an earlier approval — new artifacts
need new review). The team then moves it: in_review -> approved | changes_requested.
State lives in reports/runs/reviews.json (committable, next to the run history);
every transition is appended to the key's history.

Each key also carries a `release` (target release version): auto-captured from the
JIRA ticket's fixVersions during Workflow B, set manually for PRs.

CLI (used by pipeline.sh and bin/qa.py):
  review_state.py auto    <KEY>                    mark pending_review if this run committed
  review_state.py set     <KEY> <status> [reviewer] [note]
  review_state.py release <KEY> <version> [source] set the target release version
  review_state.py get     <KEY>
  review_state.py list

A key may also carry an advisory `critic` score (see engine/lib/critic.py). It is
attached information only — it never drives a status transition.
"""
import json, os, pathlib, sys, time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import fs_lock

VALID = ["pending_review", "in_review", "approved", "changes_requested"]
ROOT = pathlib.Path(__file__).resolve().parents[2]
FILE = pathlib.Path(os.environ.get("AIQE_REVIEWS_FILE", ROOT / "reports/runs/reviews.json"))


def load():
    # Guarded: a torn write used to make every caller RAISE — review board, wizard
    # and run records all went down until the file was hand-edited. Corrupt files
    # are quarantined by fs_lock, preserving the bytes for recovery.
    return fs_lock.read_json_guarded(FILE, {})


def save(data):
    fs_lock.write_json_atomic(FILE, data, sort_keys=True)


def set_status(key, status, reviewer="", note="", ts=None):
    if status not in VALID:
        sys.exit(f"invalid status '{status}' (valid: {', '.join(VALID)})")
    with fs_lock.lock(FILE):
        data = load()
        entry = data.get(key, {"history": []})
        entry["history"].append({"status": status, "reviewer": reviewer, "note": note,
                                 "ts": ts if ts is not None else time.time()})
        entry.update(status=status, reviewer=reviewer, note=note,
                     updated=entry["history"][-1]["ts"])
        data[key] = entry
        save(data)
    return entry


def set_release(key, release, source="manual", ts=None):
    """Record the target release version for a key (idempotent on same value)."""
    with fs_lock.lock(FILE):
        data = load()
        entry = data.get(key, {"history": []})
        if entry.get("release") == release:
            return entry
        entry["history"].append({"release": release, "source": source,
                                 "ts": ts if ts is not None else time.time()})
        entry["release"] = release
        entry.setdefault("status", "")        # release may arrive before any commit
        data[key] = entry
        save(data)
    return entry


def set_critic(key, critic, ts=None):
    """Attach the ADVISORY critic signal to a key (openhands-review §3.2).

    Deliberately never touches `status`: the critic informs whoever reviews the
    artifacts, it does not decide anything. A low score cannot move a key out of
    `approved`, and a high one cannot skip review.
    """
    with fs_lock.lock(FILE):
        data = load()
        entry = data.get(key, {"history": []})
        entry["critic"] = {"score": critic.get("score"),
                           "verdict": critic.get("verdict"),
                           "noise_count": critic.get("noise_count", 0),
                           "specs_reviewed": critic.get("specs_reviewed", 0),
                           "findings": critic.get("findings", []),
                           "rationale": critic.get("rationale", ""),
                           "ts": ts if ts is not None else time.time()}
        entry.setdefault("status", "")         # a critic score may land before any commit
        data[key] = entry
        save(data)
    return entry


def auto(key):
    """Called by the pipeline after the gate loop: any committed repo => needs review."""
    committed = False
    if os.path.exists("out/gate_results.tsv"):
        for line in open("out/gate_results.tsv"):
            if line.split("\t")[1:2] == ["committed"]:
                committed = True
    if not committed:
        return None
    current = load().get(key, {}).get("status")
    if current in ("pending_review", "in_review"):
        return None                                  # already awaiting the team
    note = "new AI-generated artifacts committed" + (
        f" (resets previous status: {current})" if current else "")
    entry = set_status(key, "pending_review", reviewer="pipeline", note=note)
    assignee = _assignee_for(key)
    if assignee:
        entry = assign(key, assignee)
    return entry


def reviewers():
    """The optional review rota from org-config (`review.reviewers: [..]`)."""
    try:
        import yaml
        cfg = yaml.safe_load(open(ROOT / "registry/org-config.yaml",
                                  encoding="utf-8")) or {}
        names = (cfg.get("review") or {}).get("reviewers") or []
        return [str(n) for n in names if str(n).strip()]
    except Exception:
        return []


def _assignee_for(key):
    """Deterministic assignment by key hash — deliberately NOT a stored cursor.

    A cursor persisted inside the store would be a non-dict value in a mapping every
    consumer iterates as {key: entry-dict}; one forgotten `.items()` loop crashes.
    Hashing the key gives a stable, evenly-spread pick with zero stored state, and
    stability is a feature: a re-committed key goes back to the reviewer who already
    has the context."""
    rota = reviewers()
    if not rota:
        return ""
    import zlib
    return rota[zlib.crc32(key.encode("utf-8")) % len(rota)]


def assign(key, assignee):
    """Record who is ASKED to review. Deliberately distinct from `reviewer`, which
    records who actually acted — assignment is a nudge, not a lock: anyone on the
    team can still approve, and the decision records the real actor."""
    with fs_lock.lock(FILE):
        data = load()
        entry = data.get(key, {"history": []})
        entry["assigned_to"] = str(assignee)
        entry["history"].append({"assigned_to": str(assignee), "ts": time.time()})
        data[key] = entry
        save(data)
    return entry


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "auto":
        e = auto(sys.argv[2])
        print(f"review-status: {sys.argv[2]} -> {e['status']}" if e
              else f"review-status: {sys.argv[2]} unchanged")
    elif cmd == "set":
        e = set_status(sys.argv[2], sys.argv[3],
                       sys.argv[4] if len(sys.argv) > 4 else "",
                       sys.argv[5] if len(sys.argv) > 5 else "")
        print(f"review-status: {sys.argv[2]} -> {e['status']}")
    elif cmd == "release":
        e = set_release(sys.argv[2], sys.argv[3],
                        sys.argv[4] if len(sys.argv) > 4 else "manual")
        print(f"release: {sys.argv[2]} -> {e['release']}")
    elif cmd == "get":
        print(json.dumps(load().get(sys.argv[2], {}), indent=2))
    elif cmd == "list":
        print(json.dumps(load(), indent=2))
    else:
        sys.exit(__doc__)
