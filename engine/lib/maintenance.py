#!/usr/bin/env python3
"""The nightly job, and whether it actually did anything.

## What was wrong

`make maintain` is what cron and the OpenShift CronJob call every night. Every
one of its twelve steps was prefixed with `-`, so make ignored failures, and
the target ended with an unconditional `@echo "== maintenance complete =="`.

Run with two steps sabotaged, the measured result was: both failed, the last
line printed was **maintenance complete**, and the exit code was **0**. A
CronJob reads the exit code. One of the two failures was the state-bundle
snapshot — the disaster-recovery backup — which could therefore fail every night
for a year while the job stayed green, and the only trace was an `Error 1
(ignored)` line buried mid-log where nobody scrolls.

That is constitution C13 at the deployment layer: an inability to perform
maintenance, reported as maintenance performed.

## Why the `-` prefixes were still right

The obvious fix — drop the `-` so make aborts — is worse. The steps are
independent, and a network blip in guidance sync must not skip the backup that
runs after it. Best-effort execution is correct. What was missing is the
REPORT: which steps ran, which failed, and an exit code that reflects it.

## The three outcomes, kept distinct

    ok         the step ran and succeeded
    degraded   the step failed, but it depends on an external system this
               platform does not own (SCM reachability, an embedding endpoint).
               NAMED in the summary, does not fail the job — the same
               distinction openhands_mode already draws, and for the same
               reason: a job that goes red on somebody else's outage every
               other night is a job whose red gets ignored.
    failed     the step is local and should have worked. The job exits 1.

The summary is printed ALWAYS, listing every step, because "which of the
twelve ran?" is the question an operator opens the log to answer and counting
`==` headers is not an answer.
"""
import pathlib
import json
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]

# (label, argv, degraded policy)
# True marks every failure as somebody else's outage. A set marks only named
# exit codes as external, so a command can still expose its own local failures.
# Keep this
# list honest: marking a local step tolerated would restore exactly the silence
# this module exists to remove.
STEPS = [
    ("guidance sync", ["engine/lib/guidance_sync.py", "sync-all"], True),
    ("prune run records", ["bin/qa.py", "prune", "--keep", "200"], False),
    ("prune OpenHands conversations", ["engine/lib/openhands_events.py", "prune"], False),
    ("prune the transaction log", ["engine/lib/event_log.py", "prune"], False),
    ("evaluate alert rules", ["engine/lib/alert_rules.py"], False),
    ("per-repo harvested facts", ["engine/lib/repo_facts.py", "rebuild"], False),
    ("knowledge chunk rebuild", ["engine/lib/knowledge_chunks.py", "rebuild"], False),
    # The embedding endpoint is external and the index falls back to TF-IDF
    # when it is unreachable, so an outage here degrades retrieval rather than
    # breaking the estate.
    ("vector index refresh", ["engine/lib/vector_index.py", "refresh"], True),
    ("cost regression check", ["engine/lib/cost_report.py", "check-regression"], False),
    # Exit 75 is the provider/Notify unavailable contract. Configuration and
    # durable-state failures use exit 1 and must still fail maintenance.
    ("cost reconciliation", ["engine/lib/cost_reconcile.py"], {75}),
    ("spec drift check", ["engine/lib/spec_drift.py", "check", "--notify"], False),
    ("coverage drift check", ["engine/lib/coverage_drift.py", "--notify"], False),
    ("state-bundle snapshot", ["engine/lib/state_bundle.py", "export"], False),
]


def run_steps(steps=None, retain_days=None, runner=None):
    """Run every step regardless of earlier failures. Returns the outcomes."""
    steps = STEPS if steps is None else steps
    results = []
    for label, argv, tolerated in steps:
        argv = list(argv)
        if argv[0].endswith("event_log.py") and retain_days:
            argv.append(str(retain_days))
        print(f"== {label} ==", flush=True)
        tail = []
        if runner is not None:
            code = runner(argv)
        else:
            # Stream AND capture. Streaming matters for the long steps (an
            # operator watching a nightly job should see progress), and
            # capturing matters because the step's own words are the only
            # place the REASON exists -- `cost_reconcile` prints
            # "ANTHROPIC_ADMIN_KEY is not configured" and the summary used to
            # discard it, leaving a CronJob log saying only "exit 75".
            proc = subprocess.Popen([sys.executable] + argv, cwd=ROOT,
                                    stdin=subprocess.DEVNULL,
                                    stdout=subprocess.PIPE,
                                    stderr=subprocess.STDOUT, text=True,
                                    errors="replace")
            for line in proc.stdout:
                print(line, end="", flush=True)
                line = line.rstrip()
                if line:
                    tail.append(line)
                    del tail[:-40]          # bounded: a chatty step cannot
                                            # grow this without limit
            code = proc.wait()
        external = (tolerated is True or
                    isinstance(tolerated, (set, frozenset, tuple, list))
                    and code in tolerated)
        state = "ok" if code == 0 else ("degraded" if external else "failed")
        results.append({"step": label, "exit": code, "state": state,
                        "command": " ".join(argv),
                        "reason": step_reason(tail) if state != "ok" else ""})
    return results


def step_reason(tail):
    """The step's own words for why it did not succeed.

    Precedence follows work_queue.failure_reason, which solved this once
    already: a structured reason the step EMITTED beats prose, and prose beats
    nothing. An exit code alone sends the reader to the source to learn that
    75 meant "no billing credential".
    """
    for line in reversed(tail):
        s = line.strip()
        if s.startswith("{") and s.endswith("}"):
            try:
                d = json.loads(s)
            except ValueError:
                continue
            if isinstance(d, dict):
                r = d.get("reason") or d.get("message")
                if r:
                    code = d.get("reason_code")
                    return f"{r} [{code}]" if code else str(r)
    for line in reversed(tail):
        s = line.strip()
        if s and not s.startswith("=="):
            return s[:200]
    return ""


def summarize(results):
    """The block an operator reads. Never omitted, never abbreviated on success
    — a summary that only appears when something is wrong trains people not to
    look for it."""
    lines = ["", "== maintenance summary =="]
    for r in results:
        mark = {"ok": "ok      ", "degraded": "DEGRADED", "failed": "FAILED  "}[r["state"]]
        lines.append(f"  {mark}  {r['step']}")
        if r["state"] != "ok":
            lines.append(f"            exit {r['exit']}: {r['command']}")
            if r.get("reason"):
                lines.append(f"            why: {r['reason']}")
    failed = [r for r in results if r["state"] == "failed"]
    degraded = [r for r in results if r["state"] == "degraded"]
    if failed:
        lines.append(f"MAINTENANCE INCOMPLETE: {len(failed)} of {len(results)} "
                     f"step(s) failed — " + ", ".join(r["step"] for r in failed))
        lines.append("  Nothing above this line was rolled back; the other steps ran.")
    elif degraded:
        lines.append(f"maintenance completed with {len(degraded)} degraded step(s) "
                     f"(external systems): " + ", ".join(r["step"] for r in degraded))
        lines.append("  These are not counted as failures, but they did NOT complete successfully.")
    else:
        lines.append(f"maintenance complete: all {len(results)} step(s) ok")
    return "\n".join(lines)


def exit_code(results):
    """The number a CronJob acts on. Its own function so it can be pinned
    directly — the first version of the test suite asserted only on the summary
    TEXT, and a mutation replacing this whole expression with `return 0` (the
    exact bug being fixed) went undetected."""
    return 1 if any(r["state"] == "failed" for r in results) else 0


def main(argv, steps=None, runner=None):
    # The summary is the whole point of this module, and it is read on a
    # Windows console where an em-dash in the failure line rendered as a
    # replacement character. Same treatment as cost_report.main().
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    retain = argv[0] if argv else None
    results = run_steps(steps, retain_days=retain, runner=runner)
    print(summarize(results), flush=True)
    return exit_code(results)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
