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
    if FILE.exists():
        return json.load(open(FILE, encoding="utf-8"))
    return []


def save(items):
    FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(FILE, "w", encoding="utf-8", newline="\n") as fh:
        json.dump(items, fh, indent=2)
        fh.write("\n")


def key_of(item):
    return (f"PR-{item['target']}-{item['pr']}" if item["mode"] == "pr"
            else item["target"])


def add(mode, target, pr=None, release="", requested_by="", inline_file=None):
    # "tests" resumes generation from an approved test plan (pipeline.sh tests <KEY>)
    if mode not in ("pr", "jira", "tests"):
        sys.exit("mode must be pr|jira|tests")
    if mode == "pr" and not pr:
        sys.exit("pr mode needs a PR number")
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
                           capture_output=True, text=True)
        with fs_lock.lock(FILE):
            items = load()
            cur = next((i for i in items if i["id"] == item["id"]), None)
            if cur:
                _mark(items, cur, status="done" if r.returncode == 0 else "failed",
                      finished=time.time(), exit_code=r.returncode)
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
