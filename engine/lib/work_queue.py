#!/usr/bin/env python3
"""Manual work queue: PR / JIRA items queued from the dashboard (or CLI) and
processed sequentially through engine/pipeline.sh.

Store: reports/runs/queue.json — [{id, mode, target, pr, release, requested_by,
status: queued|running|done|failed, ts, finished, exit_code}]. Run-record globs
must skip this file (like reviews.json).

CLI:
  work_queue.py add <pr|jira> <target> [pr_number] [release] [requested_by]
  work_queue.py list
  work_queue.py run          process every queued item (AIQE_MOCK=1 unless set)
"""
import json, os, pathlib, shutil, subprocess, sys, time

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


def add(mode, target, pr=None, release="", requested_by=""):
    if mode not in ("pr", "jira"):
        sys.exit("mode must be pr|jira")
    if mode == "pr" and not pr:
        sys.exit("pr mode needs a PR number")
    items = load()
    sig = (mode, target, str(pr or ""))
    for it in items:
        if (it["mode"], it["target"], str(it.get("pr") or "")) == sig \
                and it["status"] in ("queued", "running"):
            return it, False                       # already pending — dedupe
    item = {"id": f"q{int(time.time())}-{len(items) + 1}", "mode": mode,
            "target": target, "pr": str(pr) if pr else None, "release": release,
            "requested_by": requested_by, "status": "queued", "ts": time.time(),
            "finished": None, "exit_code": None}
    items.append(item)
    save(items)
    return item, True


def _mark(items, item, **kw):
    item.update(kw)
    save(items)


def run_all():
    """Process queued items in order. Mock mode unless AIQE_MOCK is set by the caller."""
    env = {**os.environ}
    env.setdefault("AIQE_MOCK", "1")
    processed = 0
    while True:
        items = load()                             # re-read: server may append mid-run
        item = next((i for i in items if i["status"] == "queued"), None)
        if item is None:
            break
        _mark(items, item, status="running")
        cmd = [bash_exe(), "engine/pipeline.sh", item["mode"], item["target"]]
        if item["mode"] == "pr":
            cmd.append(item["pr"])
        r = subprocess.run(cmd, cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                           capture_output=True, text=True)
        items = load()
        cur = next((i for i in items if i["id"] == item["id"]), None)
        if cur:
            _mark(items, cur, status="done" if r.returncode == 0 else "failed",
                  finished=time.time(), exit_code=r.returncode)
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
    else:
        sys.exit(__doc__)
