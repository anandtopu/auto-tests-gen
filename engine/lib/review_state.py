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
    """The board, with every entry guaranteed to BE an entry.

    Guarded: a torn write used to make every caller RAISE — review board, wizard
    and run records all went down until the file was hand-edited. Corrupt files
    are quarantined by fs_lock, preserving the bytes for recovery.

    That guard covers invalid JSON. It does NOT cover a valid file holding the
    wrong SHAPE — `{"PROJ-1": "approved"}` parses fine, and every consumer then
    calls .get() on a string. Measured: nine call sites across team_report,
    email_notify, trace, dashboard.py and qa.py do exactly that, so one
    hand-edited value took out the team report, the review-digest email, `make
    reviews` and — worst — `bin/dashboard.py`, which produces NO dashboard at
    all rather than one board being wrong.

    Fixing nine callers would leave the tenth. The shape is the store's promise,
    so it is kept here; `load_with_issues()` is for the callers that should tell
    a human what was skipped, because a silently smaller board reads as a
    smaller backlog.
    """
    return load_with_issues()[0]


def load_with_issues():
    """(entries, malformed_keys) — same read, but naming what it dropped."""
    raw = fs_lock.read_json_guarded(FILE, {})
    if not isinstance(raw, dict):
        # The whole document is the wrong shape (a list, say). No entry is
        # recoverable, and reporting an empty board as a clear one is the
        # failure this module already exists to avoid.
        return {}, ["<the review board file is not an object>"]
    good = {k: v for k, v in raw.items() if isinstance(v, dict)}
    return good, sorted(set(raw) - set(good))


def save(data):
    """Write the board, carrying forward entries `load()` hid from the caller.

    Every mutator here is `data = load()` -> change one key -> `save(data)`.
    Since load() now filters malformed entries out, saving that view would
    DELETE them from disk on the next unrelated status change — a read-time
    shape guard silently destroying state, which is the failure this module's
    own torn-write comment exists to prevent. The entry is unusable, but
    unusable is not the same as ours to throw away: it is the only remaining
    evidence of what someone wrote.

    No caller removes a top-level key (mutators only ever touch a validated
    one), so re-attaching cannot resurrect an intentional deletion.
    """
    merged = dict(data)
    raw = fs_lock.read_json_guarded(FILE, {})
    if isinstance(raw, dict):
        for k, v in raw.items():
            if k not in merged and not isinstance(v, dict):
                merged[k] = v
    fs_lock.write_json_atomic(FILE, merged, sort_keys=True)


def known_keys():
    """Keys that legitimately exist somewhere: a run record, plan lifecycle state,
    or an existing review entry (something already created it — pipeline, release
    capture, or an earlier transition). The API/CLI boundaries check against this
    so a typo'd or invented key can't put a phantom row on the team board."""
    keys = set(load())
    runs_dir = ROOT / "reports/runs"
    if runs_dir.is_dir():
        for f in runs_dir.glob("*.json"):
            if f.name in ("reviews.json", "queue.json", "hooks-seen.json"):
                continue
            try:
                k = (json.load(open(f, encoding="utf-8")).get("trigger") or {}).get("key")
                if k:
                    keys.add(k)
            except Exception:
                continue                       # a torn record never blocks the board
    try:
        import plan_state
        keys.update(e.get("key") for e in plan_state.summary() or [] if e.get("key"))
    except Exception:
        pass
    return keys


def require_known(key):
    """Boundary guard for user-initiated transitions (dashboard, qa.py). The
    pipeline's own `auto` path always follows a real run and needs no check."""
    if key not in known_keys():
        sys.exit(f"no run, plan or review recorded for '{key}' — the board tracks "
                 f"work that exists; check the key for typos (bin/qa.py status "
                 f"lists recent runs)")


def set_status(key, status, reviewer="", note="", ts=None):
    if status not in VALID:
        sys.exit(f"invalid status '{status}' (valid: {', '.join(VALID)})")
    if status == "changes_requested" and not note:
        sys.exit("changes_requested needs a note saying what to change — "
                 "pass --note (the reviewer's ask is the whole point of the status)")
    stamp = ts if ts is not None else time.time()
    with fs_lock.lock(FILE):
        data = load()
        entry = data.get(key, {"history": []})
        entry["history"].append({"status": status, "reviewer": reviewer, "note": note,
                                 "ts": stamp})
        entry.update(status=status, reviewer=reviewer, note=note,
                     updated=entry["history"][-1]["ts"])
        data[key] = entry
        save(data)
    # A6 provenance is deliberately outside the review-state lock: acquiring
    # two shared-store locks in opposite order would invite a deadlock. The
    # decision is already durable; any outcome-store failure is raised so the
    # caller cannot report that learning succeeded when it did not.
    import testcase_learning
    testcase_learning.record_review(key, status, reviewer, note, stamp)
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
                           # Provenance is stored with the score because this
                           # board outlives the run record's phases[]: without
                           # it, `qa.py trace` can only ever say "not recorded"
                           # and a mock's fixed score sits on the board looking
                           # measured. Absent when the caller could not
                           # establish it -- never defaulted to False.
                           **({"simulated": critic["simulated"]}
                              if isinstance(critic.get("simulated"), bool) else {}),
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
