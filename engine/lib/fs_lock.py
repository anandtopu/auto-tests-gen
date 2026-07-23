#!/usr/bin/env python3
"""Tiny cross-platform advisory lock (mkdir-based — atomic on POSIX and Windows).

Guards the shared state files (reviews.json, queue.json) against concurrent
writers: multiple queue workers, the dashboard server, and CLI invocations may
mutate them at the same time. Stale locks (holder died) are broken after
STALE_S seconds.
"""
import contextlib, os, pathlib, time

STALE_S = 60
# A lock dir with NO owner file is not a working holder: the creator died inside the
# microseconds-wide mkdir->write window, or a release's rmdir failed (Windows swallows
# that). Such orphans used to be UNBREAKABLE — the stale check needs owner, so every
# later acquisition timed out (seen as "Factory reset returns error": the clear died
# on TimeoutError against reports/runs/queue.json.lock). A short grace is enough,
# because a healthy holder writes owner immediately after mkdir.
ORPHAN_GRACE_S = 5


@contextlib.contextmanager
def lock(path, timeout=10.0):
    """Exclusive lock named after `path` (creates `<path>.lock/`)."""
    lockdir = pathlib.Path(str(path) + ".lock")
    deadline = time.time() + timeout
    while True:
        try:
            lockdir.mkdir(parents=True, exist_ok=False)
            (lockdir / "owner").write_text(f"{os.getpid()} {time.time()}",
                                           encoding="utf-8")
            break
        except FileExistsError:
            try:                                   # break stale locks
                owner_txt = (lockdir / "owner").read_text(encoding="utf-8")
                stamp = float(owner_txt.split()[1])
            except (OSError, IndexError, ValueError):
                owner_txt, stamp = None, None      # ownerless orphan (see above)
            if stamp is not None and time.time() - stamp > STALE_S:
                # Re-verify the owner is unchanged right before breaking — a
                # concurrent waiter may have already broken and re-acquired
                # this lock; tearing down the NEW holder would give two
                # writers. (Narrows the race window to microseconds.)
                try:
                    if (lockdir / "owner").read_text(encoding="utf-8") == owner_txt:
                        _release(lockdir)
                    continue
                except OSError:
                    pass
            elif stamp is None:
                try:                               # judge the orphan by dir age
                    if time.time() - lockdir.stat().st_mtime > ORPHAN_GRACE_S:
                        _release(lockdir)
                        continue
                except OSError:
                    pass                           # vanished between checks — retry
            if time.time() > deadline:
                raise TimeoutError(f"could not acquire {lockdir}")
            time.sleep(0.05)
    try:
        yield
    finally:
        _release(lockdir)


def _release(lockdir):
    try:
        (lockdir / "owner").unlink(missing_ok=True)
        lockdir.rmdir()
    except OSError:
        pass
