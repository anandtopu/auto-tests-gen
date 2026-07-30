#!/usr/bin/env python3
"""Manual work queue: PR / JIRA items queued from the dashboard (or CLI) and
processed sequentially through engine/pipeline.sh.

Store: reports/runs/queue.json — [{id, mode, target, pr, release, requested_by,
status: queued|running|done|failed, ts, finished, exit_code}]. Run-record globs
must skip this file (like reviews.json).

CLI:
  work_queue.py add <pr|jira> <target> [pr_number] [release] [requested_by]
  work_queue.py list
  work_queue.py run             process every queued item (AIQE_MOCK=1 unless set)
  work_queue.py requeue <id>    put a failed item back in the queue
  work_queue.py remove  <id>    delete a non-running item from the queue
"""
import json, os, pathlib, shutil, subprocess, sys, time

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import fs_lock

ROOT = pathlib.Path(__file__).resolve().parents[2]
FILE = pathlib.Path(os.environ.get("AIQE_QUEUE_FILE", ROOT / "reports/runs/queue.json"))


def bash_exe():
    """Git Bash, never WSL's System32 bash.exe (which needs a WSL distro)."""
    if os.environ.get("AIQE_BASH"):
        return os.environ["AIQE_BASH"]
    if os.name != "nt":
        return "bash"
    w = shutil.which("bash")
    if w and "system32" not in w.lower():
        return w
    git = shutil.which("git")
    if git:
        p = pathlib.Path(git).resolve().parents[1] / "bin" / "bash.exe"
        if p.exists():
            return str(p)
    for p in (r"C:\Program Files\Git\bin\bash.exe",
              r"C:\Program Files (x86)\Git\bin\bash.exe"):
        if pathlib.Path(p).exists():
            return p
    return "bash"


def load():
    # Guarded: corrupt -> quarantined + empty queue, not a crash (see fs_lock).
    return fs_lock.read_json_guarded(FILE, [])


def save(items):
    fs_lock.write_json_atomic(FILE, items)


def key_of(item):
    return (f"PR-{item['target']}-{item['pr']}" if item["mode"] == "pr"
            else item["target"])


def add(mode, target, pr=None, release="", requested_by="", inline_file=None,
        force=False):
    # "tests" resumes generation from an approved test plan (pipeline.sh tests <KEY>)
    if mode not in ("pr", "jira", "plan", "tests"):
        sys.exit("mode must be pr|jira|plan|tests")
    if mode == "pr" and not pr:
        sys.exit("pr mode needs a PR number")
    if mode == "plan" and not force:
        # Re-authoring an APPROVED plan resets it to draft — a human sign-off is
        # destroyed by one click of "Author test plan" on a key that already went
        # through review. Refuse with the alternatives; `force` is the deliberate
        # override (and `make plan` via the CLI is unaffected — that path is the
        # documented tool for an intentional re-author).
        try:
            import plan_state
            if plan_state.get(target).get("status") == "approved":
                sys.exit(f"the test plan for {target} is APPROVED — re-authoring "
                         f"would reset it to draft and destroy the sign-off. Read "
                         f"it (make plan-show KEY={target}), edit it (which "
                         f"deliberately revokes approval), or pass force=true to "
                         f"re-author anyway.")
        except SystemExit:
            raise
        except Exception:
            pass                     # no plan state readable — nothing to protect
    with fs_lock.lock(FILE):
        items = load()
        sig = (mode, target, str(pr or ""))
        for it in items:
            if (it["mode"], it["target"], str(it.get("pr") or "")) == sig \
                    and it["status"] in ("queued", "running"):
                return it, False                   # already pending — dedupe
        base, n = f"q{int(time.time())}", len(items) + 1
        while any(i["id"] == f"{base}-{n}" for i in items):   # ids must be unique
            n += 1                                            # even after removals
        item = {"id": f"{base}-{n}", "mode": mode,
                "target": target, "pr": str(pr) if pr else None, "release": release,
                "requested_by": requested_by, "status": "queued", "ts": time.time(),
                "finished": None, "exit_code": None,
                "inline_file": str(inline_file) if inline_file else None}
        items.append(item)
        save(items)
    return item, True


def _mark(items, item, **kw):
    item.update(kw)
    save(items)


# The pipeline's exit codes are a documented contract (architecture §5.8) — turn each
# into something a human can act on rather than a bare number.
EXIT_MEANING = {
    64: "bad key or mode — the target was rejected before any work started",
    75: "another run holds the pipeline lock — retry when it finishes",
    77: "budget exceeded (cost or wall-clock) — the run aborted before the gate",
    2: "gate: a change fell outside the test repo's allowed scope, or a filename "
       "used unsafe characters",
    3: "gate: the secret/PII scan rejected the generated content",
    4: "gate: a generated spec had no catalog sidecar (not born-mapped)",
    5: "gate: the generated tests did not pass when executed",
    6: "gate: the target directory is not a standalone git repository",
    7: "gate: the push failed against the remote",
}

# Lines worth surfacing verbatim: the platform's own machine-readable failures and the
# adapters' actionable errors. Matched case-sensitively — they are emitted, not typed.
_SIGNALS = ("NO_STASH_PROJECT", "PIPELINE_BUSY", "BUDGET_EXCEEDED", "INVALID_KEY",
            "INVALID_MODE", "PLAN_SNAPSHOT_MISSING", "not approved",
            "needs_clarification", "GATE_STATUS=", "clone failed", "fatal:",
            "HTTP 4", "HTTP 5", "Failed to authenticate", "error:")


def failure_reason(exit_code, stdout="", stderr="", limit=400):
    """A short, human-actionable explanation of why a queued run failed.

    Prefers a line the platform deliberately emitted (an adapter's NO_STASH_PROJECT
    beats "exit 3" every time); falls back to the exit code's documented meaning, then
    to the last non-empty output line. Always returns something non-empty for a
    non-zero exit — "we don't know" is still better said than left blank.
    """
    text = f"{stdout or ''}\n{stderr or ''}"
    lines = [l.strip() for l in text.splitlines() if l.strip()]

    for line in reversed(lines):                    # most recent signal wins
        if any(sig in line for sig in _SIGNALS):
            return line[:limit]

    meaning = EXIT_MEANING.get(exit_code)
    if meaning:
        return f"exit {exit_code} — {meaning}"
    if lines:
        return f"exit {exit_code} — {lines[-1][:limit]}"
    return f"exit {exit_code} — no output captured"


def requeue(item_id):
    """Put a failed item back in the queue (fresh attempt, previous result
    cleared). Also the recovery path for an item stranded in `running` by a
    crashed worker — nothing else can transition it."""
    with fs_lock.lock(FILE):
        items = load()
        item = next((i for i in items if i["id"] == item_id), None)
        if item is None:
            sys.exit(f"no such queue item: {item_id}")
        if item["status"] not in ("failed", "running"):
            sys.exit(f"only failed (or stranded running) items can be re-queued "
                     f"({item_id} is {item['status']})")
        _mark(items, item, status="queued", finished=None, exit_code=None, ts=time.time())
    return item


def remove(item_id):
    """Delete a queued, failed, or done item; a running item cannot be removed."""
    with fs_lock.lock(FILE):
        items = load()
        item = next((i for i in items if i["id"] == item_id), None)
        if item is None:
            sys.exit(f"no such queue item: {item_id}")
        if item["status"] == "running":
            sys.exit(f"{item_id} is running - wait for it to finish")
        save([i for i in items if i["id"] != item_id])
    return item


def prune_done(keep=50):
    """Data retention for the queue HISTORY: keep the newest `keep` done items,
    drop the rest (and their now-useless inline ticket files). Pending, running
    and failed items are never touched — they are work, not history. The run
    records a done item produced live in reports/runs/ and have their own
    retention (qa.py prune)."""
    with fs_lock.lock(FILE):
        items = load()
        done = sorted((i for i in items if i.get("status") == "done"),
                      key=lambda i: i.get("finished") or i.get("ts") or 0,
                      reverse=True)
        doomed = done[keep:]
        doomed_ids = {i["id"] for i in doomed}
        for i in doomed:
            f = i.get("inline_file")
            if f:
                pathlib.Path(f).unlink(missing_ok=True)
        save([i for i in items if i["id"] not in doomed_ids])
    return {"kept": min(len(done), keep), "removed": len(doomed)}


def run_all():
    """Process queued items in order. Mock mode unless AIQE_MOCK is set by the caller."""
    env = {**os.environ}
    env.setdefault("AIQE_MOCK", "1")
    processed = 0
    while True:
        with fs_lock.lock(FILE):                   # claim atomically: multiple workers
            items = load()                         # may drain the same queue
            item = next((i for i in items if i["status"] == "queued"), None)
            if item is not None:
                _mark(items, item, status="running")
        if item is None:
            break
        cmd = [bash_exe(), "engine/pipeline.sh", item["mode"], item["target"]]
        if item["mode"] == "pr":
            cmd.append(item["pr"])
        item_env = {**env}
        if item.get("inline_file"):                # pasted JIRA context, not a real ticket
            item_env["AIQE_INLINE_FILE"] = item["inline_file"]
        r = subprocess.run(cmd, cwd=ROOT, env=item_env, stdin=subprocess.DEVNULL,
                           capture_output=True, text=True,
                           encoding="utf-8", errors="replace")
        with fs_lock.lock(FILE):
            items = load()
            cur = next((i for i in items if i["id"] == item["id"]), None)
            if cur:
                # Persist WHY it failed. The runner is a background subprocess
                # launched by the dashboard, so its stdout/stderr goes to a console
                # no user ever reads — storing only the exit code left the UI able
                # to say nothing but "run failed", with the actionable message
                # (e.g. NO_STASH_PROJECT, PIPELINE_BUSY, a gate rejection) thrown
                # away at exactly the moment someone needed it.
                _mark(items, cur, status="done" if r.returncode == 0 else "failed",
                      finished=time.time(), exit_code=r.returncode,
                      error=("" if r.returncode == 0
                             else failure_reason(r.returncode, r.stdout, r.stderr)))
        # A release chosen at queue time is a fact about the work — persist it so
        # release-filtered views and reports include this key.
        if r.returncode == 0 and item.get("release"):
            import review_state
            review_state.set_release(key_of(item), item["release"], "queue")
        print(f"{key_of(item)}: {'done' if r.returncode == 0 else f'failed (exit {r.returncode})'}")
        if r.returncode != 0:
            print(r.stdout[-800:] + r.stderr[-800:], file=sys.stderr)
        processed += 1
    print(f"queue drained: {processed} item(s) processed")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd == "add":
        item, fresh = add(sys.argv[2], sys.argv[3],
                          sys.argv[4] if len(sys.argv) > 4 else None,
                          sys.argv[5] if len(sys.argv) > 5 else "",
                          sys.argv[6] if len(sys.argv) > 6 else "")
        print(f"{'queued' if fresh else 'already queued'}: {key_of(item)} ({item['id']})")
    elif cmd == "list":
        for it in load():
            print(f"{it['id']:<16} {it['status']:<8} {it['mode']:<5} {key_of(it):<24} "
                  f"release={it.get('release') or '-'}")
    elif cmd == "run":
        run_all()
    elif cmd == "requeue":
        item = requeue(sys.argv[2])
        print(f"re-queued: {key_of(item)} ({item['id']})")
    elif cmd == "remove":
        item = remove(sys.argv[2])
        print(f"removed: {key_of(item)} ({item['id']})")
    else:
        sys.exit(__doc__)
