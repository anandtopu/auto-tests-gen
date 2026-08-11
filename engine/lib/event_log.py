"""Append-only transaction log (observability epic, slice 1).

One record shape for every transaction the platform handles, because the value
of this log is answering questions ACROSS domains: "who approved PROJ-301, what
ran because of it, and what did it cost?" is four files today.

Three properties are load-bearing, and each exists because of a specific way
this feature could make the platform worse rather than better:

**It never breaks a run.** Logging is best-effort by construction. An unwritable
directory, a full disk or a bad value must not change any caller's exit code —
a platform that fails a $2 LLM run because it could not write a log line has
made observability a liability. Failures are reported ONCE per process (a
storm of "cannot write log" is itself an outage) and counted so
`health()` can say the log is incomplete rather than quietly losing events.

**It never records secrets.** The Settings UI writes `.env`. The event for that
transaction records WHICH KEYS changed, never their values. `_redact` is
applied to every detail, and it is a denylist by key name PLUS a length ceiling,
because the next secret-shaped field will not be named `password`.

**It is append-only, and the index is derived.** Concurrent writers are safe
because nobody rewrites a line. Anything built on top (the SQLite query index in
slice 2) is DERIVED: corrupt it and it is deleted and rebuilt, never repaired —
the same rule `vector_index.py` follows.

The `KINDS` vocabulary is closed by TEST, not at runtime: `emit()` writes
whatever kind it is given, so a new one is never silently dropped, and
`test_event_log.py` asserts every kind used in the codebase is declared here.
Rejecting at runtime would mean a typo loses the event entirely, which is the
one outcome a transaction log may not have.
"""
import datetime
import json
import os
import pathlib
import random
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import app_paths                      # R12: mutable paths resolve here

ROOT = app_paths.ROOT

# Closed vocabulary — `<domain>.<past-tense verb>`. Past tense on purpose: an
# event records something that HAPPENED. A log full of "approving"/"starting"
# invites entries for things that then did not happen.
KINDS = frozenset({
    # HTTP surface
    "request.received", "request.refused", "request.failed",
    # pipeline lifecycle
    "run.queued", "run.started", "run.phase_completed", "run.aborted",
    "run.completed",
    # the gate. `would_commit` is AIQE_GATE_CHECK_ONLY: every check ran and
    # nothing was pushed, which is NOT the same event as no_changes.
    "gate.committed", "gate.refused", "gate.no_changes", "gate.would_commit",
    # human decisions
    "plan.authored", "plan.edited", "plan.approved", "plan.revoked",
    "spec.requirements_approved", "spec.drift_detected",
    # estate configuration
    "registry.repo_added", "registry.repo_removed", "registry.mapping_changed",
    "settings.changed",
    # money. `cost.ledger_failed` is emitted by pipeline.sh and had NEVER been
    # declared here -- the closure pin scanned only Python `emit("...")` calls,
    # so shell `EV` emissions were invisible to it. The cost was concrete:
    # alert_rules reports a rule naming an undeclared kind as unknown, so an
    # operator could not alert on their spend ledger failing to write.
    "spend.phase_metered", "spend.budget_warned", "spend.budget_aborted",
    "cost.ledger_failed",
    # delivery
    "notify.sent", "notify.failed",
    "ticket.comment",
    "alert.fired", "alert.resolved",
    # the log about itself
    "log.pruned", "log.degraded",
})

OUTCOMES = ("ok", "refused", "failed", "degraded")

# Detail values are metadata, never payloads. A value longer than this is
# truncated: it is either a mistake or a secret, and both are better clipped.
MAX_VALUE_CHARS = 512
MAX_DETAIL_KEYS = 24

# Key substrings whose VALUES are never recorded. A denylist alone is not
# enough (the next secret will not be named `password`), which is why the
# length ceiling above applies to everything regardless.
_SECRET_HINTS = ("token", "secret", "password", "passwd", "api_key", "apikey",
                 "credential", "auth", "cookie", "session", "private",
                 "webhook", "signature", "bearer", "pat")

_REDACTED = "<redacted>"

# Reported once per process, not per event: a failing log that narrates its own
# failure on every call turns one problem into an outage.
_degraded_reported = False
_dropped = 0


def events_dir():
    v = (os.environ.get("AIQE_EVENTS_DIR") or "").strip()
    if v:
        return pathlib.Path(v)
    # Under `reports/`, which is a mounted volume in every deployment, so the
    # log survives a restart and works under readOnlyRootFilesystem unchanged.
    return app_paths.state_root() / "reports" / "events"


def _now():
    return datetime.datetime.now(datetime.timezone.utc)


# Per-process tie-break so ids emitted inside the same millisecond still sort
# in the order they happened. A bare random suffix does not: it produced
# out-of-order ids for 25 rapid events, which would have silently reordered
# history in a UI that pages on id. Caught by test_ids_sort_in_time_order.
_last_ms = 0
_seq = 0
# Distinguishes concurrent processes (server, receiver, pipeline) writing in the
# same millisecond. Ordering BETWEEN processes inside one millisecond is
# arbitrary and cannot be otherwise without coordination — consumers order by
# (ts, id), and the guarantee this id makes is per-process monotonicity.
_proc = random.getrandbits(16)


def _new_id(ts):
    """Sortable id: millisecond prefix, then a monotonic per-process sequence."""
    global _last_ms, _seq
    ms = int(ts.timestamp() * 1000)
    if ms == _last_ms:
        _seq += 1
    else:
        _last_ms, _seq = ms, 0
    return f"evt_{ms:013d}_{_seq:04x}_{_proc:04x}"


def _looks_secret(key):
    k = str(key).lower()
    return any(h in k for h in _SECRET_HINTS)


def redact(detail):
    """Structured metadata in, safe-to-store metadata out.

    Applied to EVERY event, not only the ones a caller thought were sensitive —
    the settings path is the obvious risk but a ticket body reaching `detail`
    would be just as bad. Nested one level, because detail is meant to be flat
    and anything deeper is a payload that does not belong here.
    """
    if detail is None:
        return None
    if not isinstance(detail, dict):
        return {"value": _clip(detail)}
    out = {}
    for i, (k, v) in enumerate(detail.items()):
        if i >= MAX_DETAIL_KEYS:
            out["_truncated_keys"] = len(detail) - MAX_DETAIL_KEYS
            break
        if _looks_secret(k):
            out[k] = _REDACTED
        elif isinstance(v, dict):
            out[k] = {kk: (_REDACTED if _looks_secret(kk) else _clip(vv))
                      for kk, vv in list(v.items())[:MAX_DETAIL_KEYS]}
        elif isinstance(v, (list, tuple)):
            out[k] = [_clip(x) for x in list(v)[:MAX_DETAIL_KEYS]]
        else:
            out[k] = _clip(v)
    return out


def _clip(v):
    if isinstance(v, (int, float, bool)) or v is None:
        return v
    s = str(v)
    return s if len(s) <= MAX_VALUE_CHARS else s[:MAX_VALUE_CHARS] + "…"


def actor_default():
    """Whoever we can honestly say did this.

    The platform has no user accounts, so this is deliberately conservative and
    SELF-DESCRIBING: callers record `actor_source` alongside, and the UI shows
    it. Inventing a username that the system cannot actually verify would make
    the audit trail worse than an honest "unknown".
    """
    for env, src in (("AIQE_ACTOR", "explicit"), ("USER", "env"),
                     ("USERNAME", "env")):
        v = (os.environ.get(env) or "").strip()
        if v:
            return v, src
    return "unknown", "unknown"


def emit(kind, actor=None, source="pipeline", target=None, run_id=None,
         outcome="ok", detail=None, duration_ms=None):
    """Append one event. Returns its id, or None if it could not be written.

    NEVER raises. Callers are pipeline phases and HTTP handlers whose real work
    must not depend on logging succeeding.
    """
    global _degraded_reported, _dropped
    try:
        ts = _now()
        if actor is None:
            actor, actor_source = actor_default()
        else:
            actor_source = "explicit"
        rec = {
            "id": _new_id(ts),
            "ts": ts.strftime("%Y-%m-%dT%H:%M:%SZ"),
            "kind": str(kind),
            "actor": str(actor),
            "actor_source": actor_source,
            "source": str(source),
            "target": _clip(target) if target is not None else None,
            "run_id": _clip(run_id) if run_id is not None else None,
            "outcome": outcome if outcome in OUTCOMES else "ok",
            "detail": redact(detail),
        }
        if duration_ms is not None:
            try:
                rec["duration_ms"] = int(duration_ms)
            except (TypeError, ValueError):
                pass
        d = events_dir()
        d.mkdir(parents=True, exist_ok=True)
        line = json.dumps(rec, ensure_ascii=False, separators=(",", ":"))
        # One open-append-close per event. Small lines appended in "a" mode do
        # not interleave in practice, and holding a handle open across a
        # long-lived server process would lose buffered events on a crash —
        # which is exactly when the log matters most.
        with open(d / f"{ts.strftime('%Y-%m-%d')}.jsonl", "a",
                  encoding="utf-8", newline="\n") as fh:
            fh.write(line + "\n")
        return rec["id"]
    except Exception as e:                      # noqa: BLE001 - see docstring
        _dropped += 1
        if not _degraded_reported:
            _degraded_reported = True
            print(f"[event-log] DEGRADED: cannot write events ({e}). "
                  f"Work continues; the transaction log is now incomplete.",
                  file=sys.stderr)
        return None


def health():
    """Whether this process has been able to log. Surfaced so a UI can say
    'the log is incomplete' rather than showing a convincing but partial
    history — principle 3, never present unmeasured as measured."""
    return {"degraded": _degraded_reported, "dropped": _dropped,
            "dir": str(events_dir())}


def _files():
    d = events_dir()
    return sorted(d.glob("*.jsonl")) if d.is_dir() else []


def read(limit=200, kinds=None, actor=None, target=None, outcome=None,
         since=None, run_id=None):
    """Newest-first events matching the filters.

    A corrupt line is SKIPPED and counted, never raised: a single bad write must
    not make the whole history unreadable. The count is returned so the caller
    can say so.
    """
    kinds = set(kinds) if kinds else None
    rows, corrupt = [], 0
    for f in reversed(_files()):
        try:
            lines = f.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            corrupt += 1
            continue
        for line in reversed(lines):
            if not line.strip():
                continue
            try:
                r = json.loads(line)
            except ValueError:
                corrupt += 1
                continue
            # Valid JSON is not necessarily an event. A scalar, list, or object
            # missing fields consumed unconditionally by the CLI/UI used to be
            # returned as a row and then crash the whole Activity surface.
            # Count it exactly like a torn JSON line so partial evidence stays
            # visible and explicitly incomplete.
            if (not isinstance(r, dict)
                    or not isinstance(r.get("ts"), str)
                    or not isinstance(r.get("kind"), str)
                    or not isinstance(r.get("outcome"), str)):
                corrupt += 1
                continue
            if kinds and r.get("kind") not in kinds:
                continue
            if actor and r.get("actor") != actor:
                continue
            if target and r.get("target") != target:
                continue
            if outcome and r.get("outcome") != outcome:
                continue
            if run_id and r.get("run_id") != run_id:
                continue
            if since and (r.get("ts") or "") < since:
                continue
            rows.append(r)
            if len(rows) >= limit:
                return rows, corrupt
    return rows, corrupt


def prune(days):
    """Drop whole day-files older than `days`. Returns the paths removed.

    Shipped WITH writing, not after: an append-only log with no retention is a
    disk-full incident scheduled for later.
    """
    if not days or days <= 0:
        return []
    cutoff = (_now() - datetime.timedelta(days=int(days))).strftime("%Y-%m-%d")
    removed = []
    for f in _files():
        if f.stem < cutoff:
            try:
                f.unlink()
                removed.append(str(f))
            except OSError:
                pass
    if removed:
        emit("log.pruned", source="cron", outcome="ok",
             detail={"files": len(removed), "older_than_days": int(days)})
    return removed


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    argv = sys.argv[1:]
    if argv and argv[0] == "--emit":
        # `event_log.py --emit <kind> [target] [outcome] [k=v k=v ...]`
        # The shell entry point. Detail arrives as k=v pairs because bash has no
        # JSON: anything richer would mean quoting rules in pipeline.sh, and a
        # logging call that can break a run on a quoting mistake defeats the
        # purpose. Unparseable pairs are kept as flags rather than dropped.
        kind = argv[1] if len(argv) > 1 else "log.degraded"
        target = argv[2] if len(argv) > 2 and argv[2] else None
        outcome = argv[3] if len(argv) > 3 and argv[3] else "ok"
        detail = {}
        for token in " ".join(argv[4:]).split():
            k, sep, v = token.partition("=")
            detail[k] = v if sep else True
        print(emit(kind, source="pipeline", target=target, outcome=outcome,
                   run_id=os.environ.get("RUN_ID") or None,
                   detail=detail or None) or "")
    elif argv and argv[0] == "prune":
        print(json.dumps(prune(int(argv[1]) if len(argv) > 1 else 30), indent=1))
    elif argv and argv[0] == "health":
        print(json.dumps(health(), indent=1))
    else:
        rows, corrupt = read(limit=int(argv[0]) if argv else 50)
        for r in rows:
            print(f"{r['ts']}  {r['outcome']:8}  {r['kind']:26}  "
                  f"{r.get('actor',''):10}  {r.get('target') or ''}")
        if corrupt:
            print(f"({corrupt} unreadable line(s) skipped)", file=sys.stderr)
