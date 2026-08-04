#!/usr/bin/env python3
"""Rate limiting for retries — because a retry is a real pipeline run.

Re-running a failed request is not free: it clones repos, calls an LLM for
every phase, and can commit. Before this, `requeue` could be pressed without
limit, and each press cost another full run. A stuck UI, an impatient user, or
a script in a loop could spend real money on a request that will fail the same
way every time.

THE LIMITS ARE THREE, and a refusal always says WHICH one it hit and WHEN the
next attempt is allowed. "Rate limited" with no number is an error message that
makes the reader guess:

    cooldown        the minimum gap between two attempts on the same key
    max_attempts    how many attempts a key gets inside the window
    window          the period those attempts are counted over

Configured under `retry:` in registry/org-config.yaml; the defaults below apply
when it is absent, so an estate that never configures anything is still bounded.

WHY ATTEMPTS ARE COUNTED PER KEY, NOT PER QUEUE ITEM: a queue item's id changes
when it is removed and re-added, so counting per item is a limit anyone can
reset by clicking Remove. The key (`PR-<repo>-<n>` or a ticket) is the thing a
user is actually retrying.

The counter is a RECORD, not a guess. It survives the process, so the limit
holds across the UI, the CLI and the API rather than being re-derived by
whichever one happens to be running.
"""
import json
import os
import pathlib
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import fs_lock  # noqa: E402

# NOT under reports/runs/: every glob over that directory has to remember to
# skip the state files that live in it, and CLAUDE.md lists three already.
# Adding a fourth trap for the sake of tidiness would be a poor trade.
#
# AIQE_RETRIES_FILE exists for the same reason AIQE_REVIEWS_FILE and
# AIQE_QUEUE_FILE do, and it was missing here. Without it the test suite had no
# way to isolate this store, so `make review` recorded its fixture attempts into
# the ESTATE's counters and spent a real operator's retry budget: measured, the
# fixture key PROJ-9 held three genuine attempts and the suite then failed
# because its own runs had exhausted the limit. A rate limiter whose counters
# anything else can fill is a rate limiter that refuses the wrong person.
FILE = pathlib.Path(os.environ.get("AIQE_RETRIES_FILE")
                    or ROOT / "reports/retries.json")

DEFAULTS = {"max_attempts": 3, "window_minutes": 60, "cooldown_seconds": 60}


def limits(root=ROOT, problems=None):
    """org-config `retry:` over the defaults.

    A malformed value falls back to ITS OWN default and is NOT treated as zero —
    a zero here would either block every retry or disable the limit entirely,
    and both are worse than a sane default.

    Validation is PER KEY. A single bad value used to escape into the blanket
    `except` and discard the whole section, so one typo silently reverted all
    three limits with nothing said. `problems` collects what was unusable so a
    caller can surface it; the limit still holds either way, because a
    misconfigured limiter must fail closed to the default, not open.
    """
    out = dict(DEFAULTS)
    said = problems if problems is not None else []
    try:
        import yaml
        cfg = yaml.safe_load((pathlib.Path(root) / "registry/org-config.yaml")
                             .read_text(encoding="utf-8")) or {}
    except Exception as e:
        said.append(f"org-config unreadable ({e}); using retry defaults")
        return out
    section = cfg.get("retry")
    if section is None:
        return out
    if not isinstance(section, dict):
        said.append("`retry:` is not a mapping; using retry defaults")
        return out
    for k, default in DEFAULTS.items():
        if k not in section:
            continue
        v = section[k]
        if isinstance(v, bool) or not isinstance(v, (int, float)) or v <= 0:
            said.append(f"retry.{k}={v!r} is not a positive number; "
                        f"using the default {default}")
            continue
        out[k] = type(default)(v)
    return out


def _load(root=ROOT):
    p = pathlib.Path(root) / "reports/retries.json" if root is not ROOT else FILE
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _path(root=ROOT):
    return pathlib.Path(root) / "reports/retries.json" if root is not ROOT else FILE


def attempts(key, root=ROOT, now=None):
    """Timestamps of attempts on this key still inside the window."""
    now = now if now is not None else time.time()
    win = limits(root)["window_minutes"] * 60
    return [t for t in (_load(root).get(key) or [])
            if isinstance(t, (int, float)) and now - t < win]


def check(key, root=ROOT, now=None):
    """May this key be retried right now?

    Returns a verdict that a UI can render verbatim: `allowed`, the limit that
    refused, how long until the next attempt, and what has been used so far.
    A refusal NAMES the limit and the wait — never a bare denial.
    """
    now = now if now is not None else time.time()
    lim = limits(root)
    recent = attempts(key, root, now)
    used, cap = len(recent), lim["max_attempts"]

    if recent:
        since = now - max(recent)
        if since < lim["cooldown_seconds"]:
            wait = int(lim["cooldown_seconds"] - since) + 1
            return {"allowed": False, "limit": "cooldown", "attempts": used,
                    "max_attempts": cap, "retry_after_seconds": wait,
                    "reason": f"a retry of {key} started {int(since)}s ago; "
                              f"attempts on one key are spaced at least "
                              f"{lim['cooldown_seconds']}s apart. Try again in "
                              f"{wait}s."}

    if used >= cap:
        oldest = min(recent)
        wait = int(lim["window_minutes"] * 60 - (now - oldest)) + 1
        return {"allowed": False, "limit": "max_attempts", "attempts": used,
                "max_attempts": cap, "retry_after_seconds": wait,
                "reason": f"{key} has already been retried {used} time(s) in the "
                          f"last {lim['window_minutes']} minute(s), which is the "
                          f"configured maximum. A run that fails the same way "
                          f"three times needs a change, not another attempt — "
                          f"open the run's failure detail. The window clears in "
                          f"{wait}s."}

    return {"allowed": True, "limit": None, "attempts": used, "max_attempts": cap,
            "retry_after_seconds": None,
            "reason": f"attempt {used + 1} of {cap} in the last "
                      f"{lim['window_minutes']} minute(s)"}


def record(key, root=ROOT, now=None):
    """Log an attempt. Locked: the UI, the CLI and the API all write here, and a
    limit that loses writes under concurrency is not a limit."""
    now = now if now is not None else time.time()
    p = _path(root)
    p.parent.mkdir(parents=True, exist_ok=True)
    with fs_lock.lock(p):
        data = _load(root)
        win = limits(root)["window_minutes"] * 60
        kept = [t for t in (data.get(key) or [])
                if isinstance(t, (int, float)) and now - t < win]
        kept.append(now)
        data[key] = kept
        # Drop keys whose attempts have all aged out, so the file does not grow
        # without bound on a busy estate.
        data = {k: v for k, v in data.items()
                if any(now - t < win for t in v if isinstance(t, (int, float)))}
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=1), encoding="utf-8")
        fs_lock.replace_atomic(tmp, p)
    return len(kept)


if __name__ == "__main__":
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    if not args:
        print(json.dumps(limits(), indent=2))
    else:
        print(json.dumps(check(args[0]), indent=2))
