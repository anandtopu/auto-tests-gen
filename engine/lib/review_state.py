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
"""
import json, os, pathlib, sys, time

VALID = ["pending_review", "in_review", "approved", "changes_requested"]
ROOT = pathlib.Path(__file__).resolve().parents[2]
FILE = pathlib.Path(os.environ.get("AIQE_REVIEWS_FILE", ROOT / "reports/runs/reviews.json"))


def load():
    if FILE.exists():
        return json.load(open(FILE, encoding="utf-8"))
    return {}


def save(data):
    FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(FILE, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(data, fh, indent=2, sort_keys=True)
        fh.write("\n")


def set_status(key, status, reviewer="", note="", ts=None):
    if status not in VALID:
        sys.exit(f"invalid status '{status}' (valid: {', '.join(VALID)})")
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
    data = load()
    entry = data.get(key, {"history": []})
    if entry.get("release") == release:
        return entry
    entry["history"].append({"release": release, "source": source,
                             "ts": ts if ts is not None else time.time()})
    entry["release"] = release
    entry.setdefault("status", "")            # release may arrive before any commit
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
    return set_status(key, "pending_review", reviewer="pipeline", note=note)


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
