#!/usr/bin/env python3
"""Tiny cross-platform advisory lock (mkdir-based — atomic on POSIX and Windows).

Guards the shared state files (reviews.json, queue.json) against concurrent
writers: multiple queue workers, the dashboard server, and CLI invocations may
mutate them at the same time. Stale locks (holder died) are broken after
STALE_S seconds.
"""
import contextlib, os, pathlib, time

STALE_S = 60


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
                owner = (lockdir / "owner").read_text(encoding="utf-8").split()
                if time.time() - float(owner[1]) > STALE_S:
                    _release(lockdir)
                    continue
            except (OSError, IndexError, ValueError):
                pass
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
