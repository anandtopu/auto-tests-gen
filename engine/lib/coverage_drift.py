#!/usr/bin/env python3
"""Coverage drift alarm (roadmap 3.4) — turn the gap report into a signal.

`make gaps` is a report someone must remember to read. Drift makes it push: each
maintenance run snapshots the per-repo uncovered-surface count, compares against
the previous snapshot, and when a repo's gaps GREW, says so — on stdout for the
maintenance log and through the Notify port so the channel hears about it.

Deliberately compares counts, not sets: renamed surface would churn a set diff
into noise, while "this repo had 3 uncovered surfaces last night and has 6 now"
is exactly the sentence a lead acts on. Shrinkage is reported quietly (good news
needs no alarm). First run establishes the baseline and alarms on nothing.

State: reports/coverage-drift.json (atomic, quarantining — fs_lock helpers).
"""
import json
import os

import pathlib
import subprocess
import sys
import time

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import fs_lock
import env_flag                     # AIQE_MOCK means what it says

FILE = pathlib.Path(os.environ.get("AIQE_DRIFT_FILE")
                    or ROOT / "reports/coverage-drift.json")


def snapshot():
    import coverage_gaps
    gaps = coverage_gaps.compute()
    return {name: len(g.get("uncovered") or []) for name, g in gaps.items()}


def check(notify=False):
    """Compare now vs the stored baseline; store now. Returns the drift report."""
    now = snapshot()
    prev_doc = fs_lock.read_json_guarded(FILE, {})
    prev = prev_doc.get("counts") or {}
    grew = {r: (prev.get(r, 0), n) for r, n in now.items()
            if r in prev and n > prev[r]}
    shrank = {r: (prev[r], now[r]) for r in now
              if r in prev and now[r] < prev[r]}

    # Deliver BEFORE advancing the baseline, and keep the old counts for any
    # repo whose growth we could not report. The first version wrote the new
    # snapshot first and swallowed every delivery error, so a channel outage
    # meant the growth was announced once to a maintenance log nobody reads,
    # the baseline moved on, and the next run saw no growth — the alarm was
    # lost permanently. "Could not tell you" is not "nothing happened", the
    # same rule the alert rules already follow.
    delivered = True
    if prev and grew and notify:
        delivered = _notify(
            "Coverage drift: uncovered surface grew in "
            + ", ".join(f"{r} ({was}->{is_})" for r, (was, is_) in sorted(grew.items()))
            + " — see make gaps")

    counts = dict(now)
    if not delivered:
        for r in grew:
            counts[r] = prev[r]          # re-alarm next run until it lands
    fs_lock.write_json_atomic(FILE, {"counts": counts, "checked": time.time()})

    report = {"baseline": not prev, "grew": grew, "shrank": shrank,
              "counts": now, "delivered": delivered}
    if not prev:
        print(f"coverage drift: baseline established for {len(now)} repo(s)")
        return report
    if grew:
        for r, (was, is_) in sorted(grew.items()):
            print(f"COVERAGE DRIFT: {r} uncovered surface grew {was} -> {is_}")
        if notify and not delivered:
            print("COVERAGE DRIFT: could not deliver the notification — "
                  "baseline NOT advanced, so the next run will report it again")
    else:
        print("coverage drift: no growth"
              + (f"; improved: {', '.join(sorted(shrank))}" if shrank else ""))
    return report


def _notify(msg):
    """Through the Notify port, mock-aware, best-effort. Returns whether it was
    DELIVERED — an unreachable channel must not fail maintenance, but it must
    not look like a successful alarm either."""
    import work_queue
    mock = env_flag.mock()
    adapter = ROOT / ("adapters/mock/notify.sh" if mock else "adapters/notify/slack.sh")
    try:
        r = subprocess.run([work_queue.bash_exe(), str(adapter), "post", msg],
                           cwd=ROOT, stdin=subprocess.DEVNULL, timeout=30,
                           capture_output=True)
        return r.returncode == 0
    except Exception:                      # noqa: BLE001
        # Still best-effort — an unreachable channel must not fail maintenance —
        # but the CALLER now learns it failed instead of the failure vanishing.
        return False


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    check(notify="--notify" in sys.argv)
